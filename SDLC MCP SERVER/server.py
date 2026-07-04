"""
server.py

Standalone SDLC MCP server — JSON-RPC over streamable-HTTP, built with FastMCP.

Run:
    python server.py

All clients connect via:
    http://{MCP_SERVER_HOST}:{MCP_SERVER_PORT}/mcp
    Authorization: Bearer <MCP_BEARER_TOKEN>

Config is env-driven (.env):
    MCP_SERVER_HOST   (default 127.0.0.1)
    MCP_SERVER_PORT   (default 8100)
    MCP_BEARER_TOKEN  (default placeholder → server unprotected, warns)
"""
import logging
import logging.config

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.auth.provider import AccessToken
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from core.settings import settings
from core.config_loader import config as _config
from registry import MCPRegistry  # registry must be imported before connectors
import connectors.jira_connector       # noqa: F401 — triggers MCPRegistry.register("jira", ...)
import connectors.github_connector     # noqa: F401
import connectors.slack_connector      # noqa: F401
import connectors.confluence_connector # noqa: F401
from constants import is_write_tool
from tools import jira_tools, github_tools, slack_tools, confluence_tools
from auth.store import TokenStore
from auth.provider import SDLCOAuthProvider
from auth.consent import handle_consent

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("sdlc_mcp_server")

_HOST = settings.MCP_SERVER_HOST
_PORT = settings.MCP_SERVER_PORT


# ── OAuth 2.1 setup ───────────────────────────────────────────────────────────
_store = TokenStore(redis_url=settings.REDIS_URL)

if settings.OAUTH_SERVICE_SECRET == "placeholder":
    logger.warning("OAUTH_SERVICE_SECRET=placeholder — service account endpoint unprotected")

_oauth_provider = SDLCOAuthProvider(store=_store, issuer_url=settings.OAUTH_ISSUER_URL)
_auth_settings = AuthSettings(
    issuer_url=settings.OAUTH_ISSUER_URL,
    resource_server_url=settings.OAUTH_ISSUER_URL,  # this server is both AS and RS
    client_registration_options=ClientRegistrationOptions(enabled=True),
    required_scopes=["mcp"],
)

_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    # Allow both local and Docker Desktop hostname so sdlc-backend container
    # (which connects via host.docker.internal) passes the Host header check.
    allowed_hosts=[
        "127.0.0.1:*", "localhost:*", "[::1]:*",
        "host.docker.internal:*",
        "sdlc-mcp-server:*",   # container-to-container via sdlc-net
    ],
    allowed_origins=[
        "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
        "http://host.docker.internal:*", "http://sdlc-mcp-server:*",
    ],
)

mcp = FastMCP(
    "sdlc-mcp",
    host=_HOST,
    port=_PORT,
    auth_server_provider=_oauth_provider,
    auth=_auth_settings,
    transport_security=_transport_security,
)


# Consent page — browser OAuth flow (external hosts: Claude Desktop, Cursor, Antigravity)
@mcp.custom_route("/oauth/consent", methods=["GET", "POST"])
async def oauth_consent(request):
    return await handle_consent(request, _store, settings.OAUTH_CONSENT_PIN)


# Service account token endpoint — machine-to-machine (ai-sdlc internal connection).
# Client POSTs { client_secret } → receives a short-lived (1hr) access token.
# Token auto-rotates: ai-sdlc fetches a fresh one before expiry. No browser needed.
@mcp.custom_route("/oauth/token/service", methods=["POST"])
async def service_token_endpoint(request):
    import secrets as _secrets
    from datetime import datetime, timedelta, timezone
    from starlette.responses import JSONResponse

    if settings.OAUTH_SERVICE_SECRET == "placeholder":
        return JSONResponse({"error": "not_configured"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    if body.get("client_secret") != settings.OAUTH_SERVICE_SECRET:
        logger.warning("OAuth service endpoint: invalid client_secret attempt")
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    _expires_in = 3600
    _token = _secrets.token_urlsafe(32)
    _store.save_access_token(AccessToken(
        token=_token,
        client_id="service-account",
        scopes=["mcp"],
        expires_at=int((datetime.now(timezone.utc) + timedelta(seconds=_expires_in)).timestamp()),
    ))
    logger.info("OAuth: issued 1hr service account token")
    return JSONResponse({"access_token": _token, "token_type": "Bearer", "expires_in": _expires_in})


# ── Diagnostic tool ───────────────────────────────────────────────────────────
@mcp.tool()
def ping(name: str = "world") -> str:
    """Health-check the SDLC MCP server. Returns a 'pong' greeting for `name`.

    Use this only to confirm the MCP server is reachable and tool-calling works.
    """
    logger.info("tool ping(name=%r) called", name)
    return f"pong: {name} — sdlc-mcp server is alive"


# ── Real tools ────────────────────────────────────────────────────────────────
_registry = MCPRegistry()

# Read tools — safe for the autonomous gather loop.
for _module in (jira_tools, github_tools, slack_tools, confluence_tools):
    _module.register(mcp, _registry)

# Write tools — state-changing. Exposed but the client gates them via is_write_tool.
for _module in (jira_tools, github_tools, slack_tools, confluence_tools):
    _module.register_writes(mcp, _registry)


# ── ToolAnnotations (MCP spec) ────────────────────────────────────────────────
# Stamp protocol-level read/write metadata so third-party hosts (Claude Desktop,
# Cursor, Antigravity) get the safety signal from tools/list.
# ponytail: one loop here beats annotations= kwarg on ~30 decorators.
def _annotate_tools() -> None:
    for _tool in mcp._tool_manager.list_tools():
        write = is_write_tool(_tool.name)
        _tool.annotations = ToolAnnotations(
            title=_tool.name,
            readOnlyHint=not write,
            destructiveHint=write,
        )
    logger.info("Stamped ToolAnnotations on %d tools", len(mcp._tool_manager.list_tools()))


_annotate_tools()


# ── MCP Resource ──────────────────────────────────────────────────────────────
@mcp.resource("jira://sprint/{project}/current", mime_type="application/json")
async def current_sprint(project: str) -> dict:
    """Current sprint board for `project` (stats, completion %, risk).

    Read-only context resource. `project` is a Jira project key (e.g. "SDLC");
    pass "default" to use the server's configured default project.
    """
    proj = "" if project.lower() == "default" else project
    logger.info("resource jira://sprint/%s/current", project)
    return await _registry.get("jira").get_sprint_board(proj)


# ── MCP Prompts ───────────────────────────────────────────────────────────────
# Starter prompts surfaced to third-party hosts (Claude Desktop, Cursor,
# Antigravity) as ready-made slash-command style prompts.
@mcp.prompt(title="Sprint risk review")
def sprint_risk_review(project: str = "default") -> str:
    """Assess current sprint delivery risk for a Jira project."""
    return _config.get_prompt("mcp_prompt_sprint_risk_review", project=project)


@mcp.prompt(title="Blocker analysis")
def blocker_analysis(project: str = "default") -> str:
    """List and prioritise everything currently blocking a project."""
    return _config.get_prompt("mcp_prompt_blocker_analysis", project=project)


@mcp.prompt(title="Release readiness")
def release_readiness(project: str = "default") -> str:
    """Go / no-go assessment of whether a project is ready to release."""
    return _config.get_prompt("mcp_prompt_release_readiness", project=project)


@mcp.prompt(title="PR review")
def pr_review(repo: str, pr_number: str) -> str:
    """Review a specific GitHub pull request and recommend approve / changes."""
    return _config.get_prompt("mcp_prompt_pr_review", repo=repo, pr_number=pr_number)


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    logger.info("Starting SDLC MCP server on http://%s:%d/mcp", _HOST, _PORT)
    logger.info("OAuth 2.1 issuer: %s", settings.OAUTH_ISSUER_URL)
    logger.info("Service account: %s", "configured" if settings.OAUTH_SERVICE_SECRET != "placeholder" else "placeholder (unprotected)")

    last_err: Exception | None = None
    # "streamable-http" = mcp SDK 1.x; "http" = newer alias — try both.
    for transport in ("streamable-http", "http"):
        try:
            mcp.run(transport=transport)
            return
        except (ValueError, KeyError) as err:
            last_err = err
            logger.warning("transport=%r not accepted (%s) — trying next", transport, err)
    raise RuntimeError(f"No supported streamable-HTTP transport found: {last_err}")


if __name__ == "__main__":
    main()
