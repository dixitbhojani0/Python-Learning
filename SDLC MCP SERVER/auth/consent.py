"""
auth/consent.py

OAuth 2.1 consent page — served at GET/POST /oauth/consent.
Public route (no auth required); registered via @mcp.custom_route in server.py.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from auth.store import TokenStore

logger = logging.getLogger(__name__)

_CODE_TTL_S = 300  # 5 minutes


def _ts(delta_s: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(seconds=delta_s)).timestamp())


def _consent_html(req_id: str, client_id: str, scopes: list[str], pin_required: bool, error: str = "") -> str:
    scope_list = ", ".join(scopes) if scopes else "mcp"
    pin_field = '<label>PIN <input type="password" name="pin" required autocomplete="off" style="margin-left:8px"></label><br><br>' if pin_required else ""
    err_html = f'<p style="color:red">{error}</p>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>SDLC MCP Server — Grant Access</title>
<style>body{{font-family:system-ui,sans-serif;max-width:420px;margin:80px auto;padding:0 20px}}
h2{{margin-bottom:4px}}p{{color:#555}}form{{margin-top:24px}}
input[type=submit]{{background:#0066cc;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer;font-size:15px}}
input[type=password]{{padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:14px}}</style>
</head><body>
<h2>SDLC MCP Server</h2>
<p>Client <strong>{client_id}</strong> is requesting access.<br>Scopes: <code>{scope_list}</code></p>
{err_html}
<form method="POST">
<input type="hidden" name="req" value="{req_id}">
{pin_field}
<input type="submit" value="Allow Access">
</form>
</body></html>"""


async def handle_consent(request: Request, store: TokenStore, pin: str) -> Response:
    if request.method == "GET":
        req_id = request.query_params.get("req", "")
        # Peek without deleting — the client index must stay intact so
        # authorize() can deduplicate parallel connections while the user
        # is looking at this page. Only POST (after Allow) deletes.
        entry = await store.get_pending_auth(req_id)
        if not entry:
            return HTMLResponse("<h3>Authorization request expired or invalid.</h3>", status_code=400)

        params = AuthorizationParams(**entry["params_json"])
        scopes = params.scopes or ["mcp"]
        return HTMLResponse(_consent_html(req_id, entry["client_id"], scopes, bool(pin)))

    # POST — user submitted the form
    form = await request.form()
    req_id = str(form.get("req", ""))
    entry = await store.pop_pending_auth(req_id)
    if not entry:
        return HTMLResponse("<h3>Authorization request expired or invalid.</h3>", status_code=400)

    params = AuthorizationParams(**entry["params_json"])

    if pin and str(form.get("pin", "")) != pin:
        await store.save_pending_auth(req_id, entry)  # put it back so user can retry
        return HTMLResponse(_consent_html(req_id, entry["client_id"], params.scopes or ["mcp"], True, "Incorrect PIN — try again."))

    # Generate authorization code (>= 160 bits of entropy per RFC 6749 §10.10)
    code_str = secrets.token_urlsafe(24)
    auth_code = AuthorizationCode(
        code=code_str,
        scopes=params.scopes or ["mcp"],
        expires_at=_ts(_CODE_TTL_S),
        client_id=entry["client_id"],
        code_challenge=params.code_challenge,
        redirect_uri=params.redirect_uri,
        redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        resource=params.resource,
    )
    await store.save_auth_code(auth_code)
    logger.info("OAuth consent: issued auth code for client=%s", entry["client_id"])

    qs: dict = {"code": code_str}
    if params.state:
        qs["state"] = params.state
    redirect_url = f"{params.redirect_uri}?{urlencode(qs)}"
    return RedirectResponse(url=redirect_url, status_code=302)
