"""
backend/api/routes/admin/mcp_servers.py

Admin REST endpoints to manage OUTBOUND MCP host connections (we act as a host
that connects to external MCP servers — GitHub Copilot, Notion, Antigravity,
self-hosted, etc.). Backs the Angular `admin/mcp-servers` page.

Routes (all guarded by get_admin_user):
  GET    /admin/mcp-servers              — list seed + admin-managed servers + tool counts
  POST   /admin/mcp-servers              — add a new server entry
  PUT    /admin/mcp-servers/{name}       — partial update (url / transport / headers)
  DELETE /admin/mcp-servers/{name}       — remove (refuses to delete the 'sdlc' seed)
  POST   /admin/mcp-servers/{name}/test  — probe `tools/list` on that server only

Persistence: rewrites `config/mcp_clients.yaml` atomically (write to .tmp, then
os.replace). The watchdog picks up the file change and the handler also calls
`reload_servers()` so the new connection is live immediately for chat queries.
"""
import logging
import os
import re
import tempfile
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel, Field

from backend.api.limiter import limiter
from backend.auth.middleware import UserContext, get_admin_user
from backend.core.config_loader import CONFIG_DIR
from backend.mcp_client.client import (
    SDLC_SEED_NAME,
    list_active_servers,
    list_all_servers_with_enabled,
    reload_servers,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_YAML_PATH = CONFIG_DIR / "mcp_clients.yaml"
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


# ── Pydantic models ───────────────────────────────────────────────────────────

class MCPServerCreate(BaseModel):
    name: str = Field(..., description="Slug-safe identifier (letters, digits, _, -)")
    url: str = Field(..., description="streamable-HTTP endpoint URL")
    transport: str = Field("streamable_http", description="Transport identifier")
    headers: dict[str, str] = Field(default_factory=dict, description="Optional auth headers")


class MCPServerUpdate(BaseModel):
    url: str | None = None
    transport: str | None = None
    headers: dict[str, str] | None = None


class MCPServerListItem(BaseModel):
    name: str
    url: str
    transport: str
    enabled: bool = True
    disabled_tool_count: int = 0
    tool_count: int | None = None       # None when status != "connected"
    status: str = "unknown"             # "connected" | "failed" | "disabled" | "unknown"
    is_seed: bool = False
    error: str | None = None


class MCPServerTestResult(BaseModel):
    ok: bool
    tool_count: int | None = None
    error: str | None = None


class MCPServerToolItem(BaseModel):
    name: str
    description: str = ""
    is_write: bool = False
    enabled: bool = True


class MCPServerToggleResult(BaseModel):
    name: str
    enabled: bool


# ── YAML I/O ──────────────────────────────────────────────────────────────────

def _read_yaml() -> dict[str, dict]:
    """Read `mcpServers` from the file (NOT the config-loader cache) so writes
    are based on the latest on-disk state, not a possibly-stale in-memory view."""
    try:
        with open(_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    servers = data.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def _write_yaml(servers: dict[str, dict]) -> None:
    """Atomic-write `mcpServers` back. tmp file in the SAME directory so the
    final os.replace is a real atomic rename (not a cross-device copy)."""
    payload = {"mcpServers": servers}
    _YAML_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="mcp_clients.", suffix=".yaml.tmp", dir=str(_YAML_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
        os.replace(tmp_path, _YAML_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _validate_name_format(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise HTTPException(
            status_code=422,
            detail="name must match ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$",
        )


def _reject_if_seed(name: str, action: str) -> None:
    """Use for actions that should never apply to the seed (e.g. POST a new
    'sdlc' entry, or PUT/DELETE the seed's url). Toggle-enabled and per-tool
    toggles are ALLOWED on the seed."""
    if name == SDLC_SEED_NAME:
        raise HTTPException(
            status_code=409,
            detail=f"'{SDLC_SEED_NAME}' is the built-in seed connection — {action} is not allowed. "
                   f"Use the enable/disable toggle instead.",
        )


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url must start with http:// or https://")


async def _count_tools(name: str, entry: dict) -> tuple[int | None, str | None]:
    """Probe a single server with a one-shot MultiServerMCPClient. Returns
    (tool_count, error_message). On any exception, tool_count is None and
    error_message describes what happened — the row stays in the list."""
    try:
        client = MultiServerMCPClient({name: {
            "transport": entry.get("transport", "streamable_http"),
            "url":       entry["url"],
            **({"headers": entry["headers"]} if entry.get("headers") else {}),
        }})
        tools = await client.get_tools()
        return len(tools), None
    except BaseException as exc:   # network / auth / shape — caller surfaces this
        if hasattr(exc, "exceptions"):
            err_msgs = [f"{type(e).__name__}: {e}" for e in exc.exceptions]
            err_msg = f"ExceptionGroup: {', '.join(err_msgs)}"
        else:
            err_msg = f"{type(exc).__name__}: {exc}"
        return None, err_msg


# ── Routes ────────────────────────────────────────────────────────────────────

@limiter.limit("30/minute")
@router.get("/admin/mcp-servers", response_model=list[MCPServerListItem])
async def list_mcp_servers(request: Request, user: UserContext = Depends(get_admin_user)):
    """List all outbound MCP connections, INCLUDING disabled ones (so the UI can
    show them with a re-enable toggle). Probes each enabled server with
    tools/list so the UI can show a live status badge."""
    logger.info("admin/mcp-servers list: requested by %s", user.name)
    all_rows = list_all_servers_with_enabled()
    active = list_active_servers()   # only enabled servers — probed
    out: list[MCPServerListItem] = []
    for row in all_rows:
        name = row["name"]
        enabled = bool(row["enabled"])
        disabled_count = len(row.get("disabled_tools") or [])
        if not enabled:
            out.append(MCPServerListItem(
                name=name,
                url=row["url"],
                transport=row.get("transport", "streamable_http"),
                enabled=False,
                disabled_tool_count=disabled_count,
                tool_count=None,
                status="disabled",
                is_seed=row["is_seed"],
                error=None,
            ))
            continue
        active_entry = active.get(name) or {
            "url": row["url"],
            "transport": row.get("transport", "streamable_http"),
        }
        count, err = await _count_tools(name, active_entry)
        out.append(MCPServerListItem(
            name=name,
            url=row["url"],
            transport=row.get("transport", "streamable_http"),
            enabled=True,
            disabled_tool_count=disabled_count,
            tool_count=count,
            status="connected" if count is not None else "failed",
            is_seed=row["is_seed"],
            error=err,
        ))
    return out


@limiter.limit("10/minute")
@router.post("/admin/mcp-servers", response_model=MCPServerListItem, status_code=201)
async def add_mcp_server(
    request: Request,
    body: MCPServerCreate,
    user: UserContext = Depends(get_admin_user),
):
    """Add a new outbound MCP server connection."""
    _validate_name_format(body.name)
    _reject_if_seed(body.name, "create")
    _validate_url(body.url)
    servers = _read_yaml()
    if body.name in servers:
        raise HTTPException(status_code=409, detail=f"server '{body.name}' already exists")
    entry: dict[str, Any] = {"url": body.url, "transport": body.transport}
    if body.headers:
        entry["headers"] = body.headers
    servers[body.name] = entry
    _write_yaml(servers)
    # Watchdog hot-reloads the config; reload_servers() drops the client cache
    # so the next request rebuilds with the new entry immediately.
    reload_servers()
    logger.info("admin/mcp-servers add: %s → %s (by %s)", body.name, body.url, user.name)
    count, err = await _count_tools(body.name, entry)
    return MCPServerListItem(
        name=body.name,
        url=body.url,
        transport=body.transport,
        tool_count=count,
        status="connected" if count is not None else "failed",
        is_seed=False,
        error=err,
    )


@limiter.limit("10/minute")
@router.put("/admin/mcp-servers/{name}", response_model=MCPServerListItem)
async def update_mcp_server(
    request: Request,
    name: str,
    body: MCPServerUpdate,
    user: UserContext = Depends(get_admin_user),
):
    """Partial update of an existing connection. Refuses to touch the seed
    (url/transport/headers come from env for the seed; use toggle-enabled or
    the per-tool toggle for seed customisation)."""
    _validate_name_format(name)
    _reject_if_seed(name, "update")
    servers = _read_yaml()
    if name not in servers:
        raise HTTPException(status_code=404, detail=f"server '{name}' not found")
    entry = dict(servers[name])
    if body.url is not None:
        _validate_url(body.url)
        entry["url"] = body.url
    if body.transport is not None:
        entry["transport"] = body.transport
    if body.headers is not None:
        if body.headers:
            entry["headers"] = body.headers
        else:
            entry.pop("headers", None)
    servers[name] = entry
    _write_yaml(servers)
    reload_servers()
    logger.info("admin/mcp-servers update: %s (by %s)", name, user.name)
    count, err = await _count_tools(name, entry)
    return MCPServerListItem(
        name=name,
        url=entry["url"],
        transport=entry.get("transport", "streamable_http"),
        tool_count=count,
        status="connected" if count is not None else "failed",
        is_seed=False,
        error=err,
    )


@limiter.limit("10/minute")
@router.delete("/admin/mcp-servers/{name}", status_code=204)
async def delete_mcp_server(
    request: Request,
    name: str,
    user: UserContext = Depends(get_admin_user),
):
    """Delete an outbound connection.

    - Non-seed: removes the YAML entry entirely.
    - Seed (`sdlc`): hard-deleting is impossible (the url comes from env), so we
      SOFT-DELETE by setting enabled=false. The seed reappears as disabled in
      the list; the admin can re-enable it via toggle-enabled.
    """
    _validate_name_format(name)
    servers = _read_yaml()

    if name == SDLC_SEED_NAME:
        entry = servers.get(name) or {}
        entry["enabled"] = False
        servers[name] = entry
        _write_yaml(servers)
        reload_servers()
        logger.info("admin/mcp-servers disable seed: %s (by %s) — agents will degrade to RAG-only", name, user.name)
        return None

    if name not in servers:
        raise HTTPException(status_code=404, detail=f"server '{name}' not found")
    del servers[name]
    _write_yaml(servers)
    reload_servers()
    logger.info("admin/mcp-servers delete: %s (by %s)", name, user.name)
    return None


@limiter.limit("30/minute")
@router.post("/admin/mcp-servers/{name}/toggle-enabled", response_model=MCPServerToggleResult)
async def toggle_mcp_server_enabled(
    request: Request,
    name: str,
    user: UserContext = Depends(get_admin_user),
):
    """Flip a server's enabled state. Works for both seed and non-seed entries.

    Disabling the seed degrades the app to RAG-only — every existing chat path
    still works, but no live Jira/GitHub/Slack/Confluence data and no write
    actions until it's re-enabled.
    """
    _validate_name_format(name)
    servers = _read_yaml()
    entry: dict[str, Any] = servers.get(name) or {}
    if name != SDLC_SEED_NAME and not entry.get("url"):
        raise HTTPException(status_code=404, detail=f"server '{name}' not found")
    new_enabled = not bool(entry.get("enabled", True))
    entry["enabled"] = new_enabled
    servers[name] = entry
    _write_yaml(servers)
    reload_servers()
    logger.info("admin/mcp-servers toggle-enabled: %s → %s (by %s)", name, new_enabled, user.name)
    return MCPServerToggleResult(name=name, enabled=new_enabled)


@limiter.limit("30/minute")
@router.get("/admin/mcp-servers/{name}/tools", response_model=list[MCPServerToolItem])
async def list_mcp_server_tools(
    request: Request,
    name: str,
    user: UserContext = Depends(get_admin_user),
):
    """List the tools exposed by ONE server, with each tool's enabled state.

    Probes the server directly (one-shot client) so the list reflects what the
    server is reporting right now, not the cached aggregate. Tool `enabled` is
    derived from the server's `disabled_tools` field in YAML.
    """
    from backend.mcp.constants import is_write_tool
    yaml_entries = _read_yaml()
    yaml_entry = yaml_entries.get(name) or {}
    disabled = set(yaml_entry.get("disabled_tools") or [])

    active = list_active_servers()
    probe_entry: dict[str, Any] | None = active.get(name)
    if probe_entry is None:
        # Server is disabled (or unknown) — try to probe via YAML if it has a url.
        if not yaml_entry.get("url") and name != SDLC_SEED_NAME:
            raise HTTPException(status_code=404, detail=f"server '{name}' not found")
        # For a disabled seed we can still probe via the env URL.
        probe_entry = {
            "transport": yaml_entry.get("transport", "streamable_http"),
            "url":       yaml_entry.get("url") or os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8100/mcp"),
            **({"headers": yaml_entry["headers"]} if yaml_entry.get("headers") else {}),
        }

    try:
        client = MultiServerMCPClient({name: probe_entry})
        tools = await client.get_tools()
    except BaseException as exc:
        if hasattr(exc, "exceptions"):
            err_msgs = [f"{type(e).__name__}: {e}" for e in exc.exceptions]
            err_msg = f"ExceptionGroup: {', '.join(err_msgs)}"
        else:
            err_msg = f"{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=f"could not list tools: {err_msg}")

    out: list[MCPServerToolItem] = []
    for t in tools:
        out.append(MCPServerToolItem(
            name=t.name,
            description=(getattr(t, "description", "") or "")[:300],
            is_write=is_write_tool(t.name),
            enabled=t.name not in disabled,
        ))
    return out


@limiter.limit("30/minute")
@router.post("/admin/mcp-servers/{name}/tools/{tool}/toggle", response_model=MCPServerToggleResult)
async def toggle_mcp_tool(
    request: Request,
    name: str,
    tool: str,
    user: UserContext = Depends(get_admin_user),
):
    """Toggle a single tool on/off for a given server.

    Disabled tools are filtered out of the gather loop AND the autonomous tool
    catalogue, so the LLM cannot pick them. Re-enable to bring them back.
    """
    _validate_name_format(name)
    logger.info("admin/mcp-servers tool toggle request: %s/%s (by %s)", name, tool, user.name)
    servers = _read_yaml()
    # Seed may not have a YAML entry yet (until something is disabled on it).
    if name != SDLC_SEED_NAME and name not in servers:
        raise HTTPException(status_code=404, detail=f"server '{name}' not found")
    entry: dict[str, Any] = servers.get(name) or {}
    disabled = list(entry.get("disabled_tools") or [])
    if tool in disabled:
        disabled.remove(tool)
        new_enabled = True
    else:
        disabled.append(tool)
        new_enabled = False
    entry["disabled_tools"] = disabled
    servers[name] = entry
    _write_yaml(servers)
    reload_servers()
    logger.info("admin/mcp-servers tool toggle: %s/%s → enabled=%s", name, tool, new_enabled)
    return MCPServerToggleResult(name=tool, enabled=new_enabled)


@limiter.limit("30/minute")
@router.post("/admin/mcp-servers/{name}/test", response_model=MCPServerTestResult)
async def test_mcp_server(
    request: Request,
    name: str,
    user: UserContext = Depends(get_admin_user),
):
    """Probe one server (seed or admin-managed) with tools/list and report back."""
    active = list_active_servers()
    if name not in active:
        raise HTTPException(status_code=404, detail=f"server '{name}' not found")
    logger.info("admin/mcp-servers test: %s (by %s)", name, user.name)
    count, err = await _count_tools(name, active[name])
    return MCPServerTestResult(ok=count is not None, tool_count=count, error=err)
