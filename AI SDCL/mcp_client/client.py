"""
backend/mcp_client/client.py

MCP client integration for the host, built on langchain-mcp-adapters'
MultiServerMCPClient. Connects to our SDLC MCP server (and, later, official
vendor servers), discovers tools via `tools/list`, and exposes them as
LangChain tools the LLM can select and call.

Decoupling contract:
    await get_mcp_tools()  ->  list[BaseTool]
Callers (the tool-use node / agents) get ready-to-bind tools and nothing else —
no transport, no URLs, no server topology. Add a server here, every agent gains
its tools with zero agent-code change (the MCP discovery payoff).

Config is env-driven (no hardcoded URLs):
    MCP_SERVER_URL   full streamable-HTTP endpoint
                     (default http://127.0.0.1:8100/mcp, matching mcp_server/server.py)

Session/connection model (B7d): the MultiServerMCPClient is a process singleton and
tool SCHEMAS are fetched once via `tools/list` and cached (`_all_tools_cache`), so we
never re-list per request. Individual tool *invocations* open a short-lived streamable
-HTTP session each — that is the correct STATELESS pattern for a concurrent web host:
a single shared long-lived MCP session would be single-flight and unsafe under parallel
requests. Call `clear_tools_cache()` after the server's tool set changes (e.g. redeploy).
"""
import json
import logging
import os

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

# Our own SDLC MCP server. Each entry is one MCP server connection.
# transport "streamable_http" (underscore) = streamable-HTTP in langchain-mcp-adapters
# 0.1.x (our pin). Newer 0.3.x also accepts "http" — change here if we upgrade.
_SERVERS: dict[str, dict] = {
    "sdlc": {
        "transport": "streamable_http",
        "url": os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8100/mcp"),
    },
    # STEP 6 (stretch): add an official server, e.g.
    # "github": {"transport": "streamable_http", "url": "https://api.githubcopilot.com/mcp/"},
}

# ── Read vs write classification (safety) ────────────────────────────────────
# The autonomous gather loop must NEVER see write tools — otherwise the LLM could
# create/assign/approve without human approval. Writes are identified by verb in
# the tool name (our naming convention) and are excluded from the gather catalogue
# by default; they're reachable ONLY via call_mcp_tool() from the approved HITL
# execution path. Fail-safe: an unrecognized tool that happens to contain a write
# verb is excluded from autonomous use (safe direction).
_WRITE_VERBS = (
    "create", "update", "delete", "assign", "reassign", "deassign",
    "approve", "reject", "merge", "send", "post", "close",
)


def is_write_tool(name: str) -> bool:
    """True if a tool name denotes a state-changing (write) action."""
    n = name.lower()
    return any(v in n for v in _WRITE_VERBS)


_client: MultiServerMCPClient | None = None
_all_tools_cache: list | None = None


def _get_client() -> MultiServerMCPClient:
    """Lazily build the (stateless) MultiServerMCPClient singleton."""
    global _client
    if _client is None:
        _client = MultiServerMCPClient(_SERVERS)
        logger.info("MCP client configured for servers: %s", list(_SERVERS))
    return _client


async def _fetch_all_tools(force_refresh: bool = False) -> list:
    """Run `tools/list` once and cache every tool (read + write)."""
    global _all_tools_cache
    if _all_tools_cache is not None and not force_refresh:
        return _all_tools_cache
    tools = await _get_client().get_tools()
    _all_tools_cache = tools
    reads = [t.name for t in tools if not is_write_tool(t.name)]
    writes = [t.name for t in tools if is_write_tool(t.name)]
    logger.info("MCP tools/list → %d tools (%d read, %d write). reads=%s writes=%s",
                len(tools), len(reads), len(writes), reads, writes)
    return tools


async def get_mcp_tools(include_writes: bool = False, force_refresh: bool = False) -> list:
    """
    Return MCP tools as LangChain tools.

    Default (include_writes=False) returns READ-ONLY tools — this is what the
    autonomous gather loop uses, so the LLM cannot trigger a write. The approved
    HITL execution path uses call_mcp_tool() instead.
    """
    tools = await _fetch_all_tools(force_refresh)
    if include_writes:
        return tools
    return [t for t in tools if not is_write_tool(t.name)]


def normalize_tool_result(result: object) -> object:
    """
    Coerce MCP tool output into consistent structured data (B7f).

    MCP returns content as text, so some tools surface as a JSON *string* (or a
    list of JSON strings, e.g. github_list_open_prs) while others come back as
    dicts. Parse JSON-looking strings so callers always get clean dict/list, not
    stringified JSON. Non-JSON strings pass through untouched.
    """
    if isinstance(result, str):
        s = result.strip()
        if s[:1] in ("{", "[") and s[-1:] in ("}", "]"):
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return result
        return result
    if isinstance(result, list):
        return [normalize_tool_result(x) for x in result]
    return result


async def call_mcp_tool(name: str, args: dict) -> object:
    """
    Invoke a single MCP tool by name (read OR write) and return its NORMALIZED result.

    Used by specialist agents (deterministic tool needs) and the HITL execution
    path to run a specific tool over MCP. Raises KeyError if the tool isn't found.
    """
    tools = await _fetch_all_tools()
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        raise KeyError(f"MCP tool {name!r} not found. Available: {[t.name for t in tools]}")
    logger.info("call_mcp_tool: %s(%s)", name, args)
    return normalize_tool_result(await tool.ainvoke(args))


def clear_tools_cache() -> None:
    """Drop the cached tool schemas so the next call re-runs `tools/list` (B7d).

    Use after the MCP server's tool set changes (redeploy / new connector) so the
    host rediscovers tools without a process restart.
    """
    global _all_tools_cache
    _all_tools_cache = None
    logger.info("MCP tools cache cleared — next call will re-discover via tools/list")
