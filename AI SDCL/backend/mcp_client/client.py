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

is_write_tool() is imported from backend.mcp.constants — the shared source of
truth also used by the MCP server to stamp ToolAnnotations.  Do NOT redefine
it here; change security.yaml > tool_safety.write_verbs to add new verbs.

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
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

# Outbound MCP server connections.
#   - The 'sdlc' SEED entry is always present (our own MCP server, env-driven URL).
#   - Additional entries come from config/mcp_clients.yaml — admin-managed via the
#     /admin/mcp-servers REST API and the Angular MCP Servers admin page. Shape
#     matches the Claude Desktop / Cursor / Antigravity standard.
# transport "streamable_http" (underscore) = streamable-HTTP in langchain-mcp-adapters
# 0.1.x (our pin). Newer 0.3.x also accepts "http" — change here if we upgrade.
_SDLC_SEED_NAME = "sdlc"


def _read_yaml_entries() -> dict[str, dict]:
    """Read the raw `mcpServers` dict from mcp_clients.yaml (direct file read,
    not via the config-loader cache — see _load_servers for the why)."""
    from pathlib import Path
    import yaml
    yaml_path = Path(__file__).resolve().parents[2] / "config" / "mcp_clients.yaml"
    if not yaml_path.is_file():
        return {}
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        entries = data.get("mcpServers") or {}
        return entries if isinstance(entries, dict) else {}
    except Exception:
        logger.exception("MCP client: failed to read mcp_clients.yaml")
        return {}


def _load_servers() -> dict[str, dict]:
    """Build the active MCP server dict: seed (sdlc) + admin-managed entries.

    We read `mcp_clients.yaml` DIRECTLY from disk here rather than via the
    config-loader cache, because the admin REST handler writes the file and
    expects subsequent reads (in the same request cycle) to see the new entries
    — the watchdog reload is ~1 second behind, too slow for that round-trip.

    The seed is non-removable so the local stack always works even when the
    admin-managed list is empty. For the seed, its url and transport defaults
    come from YAML and can be overridden by the environment.
    """
    yaml_entries = _read_yaml_entries()
    servers: dict[str, dict] = {}

    for name, entry in yaml_entries.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", True):
            continue   # disabled entries are kept in YAML but not handed to the client

        url = entry.get("url")
        if name == _SDLC_SEED_NAME:
            url = os.getenv("MCP_SERVER_URL") or url

        if not url:
            continue

        out: dict[str, Any] = {
            "transport": entry.get("transport", "streamable_http"),
            "url":       url,
        }
        if isinstance(entry.get("headers"), dict) and entry["headers"]:
            out["headers"] = entry["headers"]
        servers[name] = out
    return servers


def list_all_servers_with_enabled() -> list[dict]:
    """Return every server entry — INCLUDING those disabled in YAML — so the
    admin UI can show a row with a toggle to re-enable."""
    yaml_entries = _read_yaml_entries()
    out: list[dict] = []

    for name, entry in yaml_entries.items():
        if not isinstance(entry, dict):
            continue

        url = entry.get("url")
        if name == _SDLC_SEED_NAME:
            url = os.getenv("MCP_SERVER_URL") or url

        if not url:
            continue

        out.append({
            "name":           name,
            "url":            url,
            "transport":      entry.get("transport", "streamable_http"),
            "enabled":        bool(entry.get("enabled", True)),
            "disabled_tools": list(entry.get("disabled_tools") or []),
            "is_seed":        (name == _SDLC_SEED_NAME),
        })
    return out


def disabled_tool_names() -> set[str]:
    """Union of every server's disabled_tools list (read from YAML). A tool
    whose name is in this set is hidden from the gather loop and from agents —
    same effect as if the server hadn't reported it."""
    out: set[str] = set()
    for entry in _read_yaml_entries().values():
        if not isinstance(entry, dict):
            continue
        for name in entry.get("disabled_tools") or []:
            if isinstance(name, str):
                out.add(name)
    return out

# ── Read vs write classification (safety) ────────────────────────────────────
# Imported from the shared constants module so the server's ToolAnnotations and
# this client's autonomous-loop gate always use the identical classifier.
# The verb list lives in config/security.yaml > tool_safety.write_verbs.
from backend.mcp.constants import is_write_tool  # noqa: E402  (after stdlib imports)


_client: MultiServerMCPClient | None = None
_all_tools_cache: list | None = None


def _get_client() -> MultiServerMCPClient:
    """Lazily build the (stateless) MultiServerMCPClient singleton."""
    global _client
    if _client is None:
        servers = _load_servers()
        _client = MultiServerMCPClient(servers)
        logger.info("MCP client configured for servers: %s", list(servers))
    return _client


def reload_servers() -> None:
    """Drop the cached MCP client + tools so the next call rebuilds against the
    current `mcp_clients.yaml`. Called by the admin REST handlers after a
    server is added / updated / deleted, so the change is live immediately.
    """
    global _client, _all_tools_cache
    _client = None
    _all_tools_cache = None
    logger.info("MCP client: server list reloaded — next call will rebuild")


def list_active_servers() -> dict[str, dict]:
    """Return the current server dict (seed + admin entries). Read-only view used
    by the admin route to list connections without rebuilding the client."""
    return _load_servers()


# Public reference to the seed name so admin code can refuse to delete it.
SDLC_SEED_NAME = _SDLC_SEED_NAME


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

    Admin-disabled tools (per-server `disabled_tools` list in mcp_clients.yaml)
    are filtered out so a user can turn off individual tools from the admin UI.
    """
    tools = await _fetch_all_tools(force_refresh)
    blocked = disabled_tool_names()
    if blocked:
        tools = [t for t in tools if t.name not in blocked]
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
