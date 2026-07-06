"""
backend/mcp_client/client.py

MCP client integration for the host, built on langchain-mcp-adapters'
MultiServerMCPClient. Connects to one or more MCP servers (our own SDLC server
plus any admin-managed external servers), discovers tools via `tools/list`, and
exposes them as LangChain tools the LLM can select and call.

Decoupling contract:
    await get_mcp_tools()  ->  list[BaseTool]
Callers (the tool-use node / agents) get ready-to-bind tools and nothing else —
no transport, no URLs, no server topology. Add a server in the Admin UI, every
agent gains its tools with zero agent-code change (the MCP discovery payoff).

All server connection details (url, transport, headers/auth) live in
config/mcp_clients.yaml and are managed at runtime via the Admin UI
(/admin/mcp-servers). No server is special-cased in code — the 'sdlc' server
is just the first entry in YAML, treated identically to any external MCP server.

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
import time
from typing import Any

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

# ── Service-account token cache ───────────────────────────────────────────────
# Stores (token, expiry_unix_ts). Refreshed automatically when within 60s of expiry.
_service_token: tuple[str, float] | None = None


def _get_service_token(base_url: str) -> str | None:
    """Return a cached (or freshly fetched) short-lived OAuth service token.

    POSTs {client_secret} to <base_url>/oauth/token/service; result is cached
    until 60s before expiry, then auto-refreshed. `base_url` is the server's
    URL with the /mcp path stripped — passed from _load_servers() so the token
    fetch always uses the same host as the MCP connection (local vs Docker).
    Returns None when MCP_SERVICE_SECRET is unconfigured.
    """
    global _service_token
    from backend.core.settings import settings  # late import avoids circular dep at module init

    secret = settings.MCP_SERVICE_SECRET
    if not secret or secret == "placeholder":
        return None

    now = time.time()
    if _service_token is not None:
        token, expiry = _service_token
        if now < expiry - 60:
            return token  # still valid

    try:
        resp = httpx.post(
            f"{base_url}/oauth/token/service",
            json={"client_secret": secret},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        _service_token = (token, now + int(data.get("expires_in", 3600)))
        logger.info("MCP service token refreshed (expires_in=%s)", data.get("expires_in"))
        return token
    except Exception:
        logger.exception("MCP service token fetch failed — using stale/no token")
        return _service_token[0] if _service_token else None


def _is_service_token_expiring() -> bool:
    """True when the cached service token is within 60s of expiry (time to rebuild client)."""
    if _service_token is None:
        return False
    _, expiry = _service_token
    return time.time() >= expiry - 60


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
    """Build the active MCP server dict from mcp_clients.yaml.

    We read `mcp_clients.yaml` DIRECTLY from disk here rather than via the
    config-loader cache, because the admin REST handler writes the file and
    expects subsequent reads (in the same request cycle) to see the new entries
    — the watchdog reload is ~1 second behind, too slow for that round-trip.

    Every server — including 'sdlc' — is treated identically: url, transport,
    and headers all come from YAML. No server is special-cased in code.
    """
    yaml_entries = _read_yaml_entries()
    servers: dict[str, dict] = {}

    for name, entry in yaml_entries.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", True):
            continue   # disabled entries are kept in YAML but not handed to the client

        url = entry.get("url")
        if not url:
            continue

        out: dict[str, Any] = {
            "transport": entry.get("transport", "streamable_http"),
            "url":       url,
        }
        if entry.get("auth") == "service":
            # Derive token endpoint from the same URL the client connects to,
            # so local (127.0.0.1) and Docker (host.docker.internal) both work.
            base_url = url.removesuffix("/mcp")
            token = _get_service_token(base_url)
            if token:
                out["headers"] = {"Authorization": f"Bearer {token}"}
        elif isinstance(entry.get("headers"), dict) and entry["headers"]:
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
        if not url:
            continue

        out.append({
            "name":           name,
            "url":            url,
            "transport":      entry.get("transport", "streamable_http"),
            "enabled":        bool(entry.get("enabled", True)),
            "disabled_tools": list(entry.get("disabled_tools") or []),
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
    """Lazily build the (stateless) MultiServerMCPClient singleton.

    Proactively rebuilds when the service-account token is within 60s of expiry
    so the new client gets a fresh token injected via _load_servers().
    """
    global _client
    if _client is not None and _is_service_token_expiring():
        logger.info("MCP service token expiring — rebuilding client with fresh token")
        reload_servers()  # sets _client = None, _all_tools_cache = None
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




async def _fetch_all_tools(force_refresh: bool = False) -> list:
    """Run `tools/list` once and cache every tool (read + write).

    _get_client() is called unconditionally (even on a cache hit) because that's
    where the service-token expiry check lives — it proactively rebuilds the
    client (and drops _all_tools_cache) when the token is within 60s of expiry.
    Skipping this call on cache hits was the bug: it let the refresh check go
    dead the moment tools were first cached, so the token just expired every
    hour with nothing rechecking it.
    """
    global _all_tools_cache
    _get_client()
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


def _is_auth_error(exc: BaseException) -> bool:
    """True if exc (or anything nested in an ExceptionGroup) is an HTTP 401.

    The MCP server's token store is Redis-backed with no persistence — a server/Redis
    restart silently wipes every issued token. Our local _service_token cache only
    checks its own wall-clock expiry, so it keeps re-sending a token Redis no longer
    has for up to an hour after such a restart. Detect by status code (not message
    text) so this survives library/wording changes.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 401
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_auth_error(e) for e in exc.exceptions)
    return False


async def ainvoke_tool(tool, args: dict) -> object:
    """Invoke an MCP tool; on 401 force a fresh service token + client and retry once.

    Shared by call_mcp_tool() and the gather_via_tools loop (tool_use.py) — the only
    two places that call tool.ainvoke() — so a token invalidated out from under us
    (see _is_auth_error) self-heals on the very next call instead of waiting for the
    stale token's wall-clock expiry.
    """
    try:
        return await tool.ainvoke(args)
    except BaseException as exc:
        if not _is_auth_error(exc):
            raise
        logger.warning("MCP tool %s got 401 — service token stale, refreshing and retrying once", tool.name)
        global _service_token
        _service_token = None
        reload_servers()
        tools = await _fetch_all_tools(force_refresh=True)
        fresh = next((t for t in tools if t.name == tool.name), tool)
        return await fresh.ainvoke(args)


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
    return normalize_tool_result(await ainvoke_tool(tool, args))


def clear_tools_cache() -> None:
    """Drop the cached tool schemas so the next call re-runs `tools/list` (B7d).

    Use after the MCP server's tool set changes (redeploy / new connector) so the
    host rediscovers tools without a process restart.
    """
    global _all_tools_cache
    _all_tools_cache = None
    logger.info("MCP tools cache cleared — next call will re-discover via tools/list")
