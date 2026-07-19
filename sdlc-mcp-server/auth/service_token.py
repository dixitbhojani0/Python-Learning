"""
auth/service_token.py

Machine-to-machine token endpoint — POST /oauth/token/service.

The ai-sdlc backend POSTs { client_secret } and receives a short-lived (1hr)
access token; it refetches before expiry, so no browser/PKCE flow is needed.
Registered via @mcp.custom_route in server.py.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth.store import TokenStore

logger = logging.getLogger(__name__)

_TOKEN_TTL_S = 3600  # 1 hour


async def handle_service_token(request: Request, store: TokenStore, service_secret: str) -> JSONResponse:
    if service_secret == "placeholder":
        return JSONResponse({"error": "not_configured"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    # compare_digest: constant-time — a plain != leaks secret length/prefix timing.
    if not secrets.compare_digest(str(body.get("client_secret", "")), service_secret):
        logger.warning("OAuth service endpoint: invalid client_secret attempt")
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    token = secrets.token_urlsafe(32)
    await store.save_access_token(AccessToken(
        token=token,
        client_id="service-account",
        scopes=["mcp"],
        expires_at=int((datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_TTL_S)).timestamp()),
    ))
    logger.info("OAuth: issued 1hr service account token")
    return JSONResponse({"access_token": token, "token_type": "Bearer", "expires_in": _TOKEN_TTL_S})
