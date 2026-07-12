"""
auth/provider.py

OAuth 2.1 + PKCE authorization server — implements OAuthAuthorizationServerProvider.
All state delegates to TokenStore. No extra dependencies.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from auth.store import TokenStore

logger = logging.getLogger(__name__)

_ACCESS_TTL_S  = 3600        # 1 hour
_REFRESH_TTL_S = 30 * 86400  # 30 days
_CODE_TTL_S    = 300         # 5 minutes


def _ts(delta_s: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(seconds=delta_s)).timestamp())


class SDLCOAuthProvider(OAuthAuthorizationServerProvider):
    def __init__(self, store: TokenStore, issuer_url: str) -> None:
        self._store = store
        self._issuer_url = issuer_url.rstrip("/")

    # ── client registration ───────────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.save_client(client_info)
        logger.info("OAuth: registered client %r", client_info.client_id)

    # ── authorization code flow ───────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        # Deduplicate: if a consent flow is already in-progress for this client,
        # return the same URL so parallel connections don't open multiple browser tabs.
        existing = self._store.find_pending_req_by_client(client.client_id)
        if existing:
            logger.info("OAuth: reusing pending consent req=%s client=%s", existing, client.client_id)
            return f"/oauth/consent?req={existing}"

        req_id = secrets.token_urlsafe(24)
        self._store.save_pending_auth(req_id, {
            "client_id":   client.client_id,
            "params_json": params.model_dump(mode="json"),
        })
        logger.info("OAuth: pending consent req=%s client=%s", req_id, client.client_id)
        return f"/oauth/consent?req={req_id}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self._store.get_auth_code(authorization_code)
        if code and code.client_id != client.client_id:
            logger.warning("OAuth: auth code client mismatch — rejecting")
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        access_token  = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        scopes = authorization_code.scopes or ["mcp"]

        self._store.save_access_token(AccessToken(
            token=access_token,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=_ts(_ACCESS_TTL_S),
        ))
        self._store.save_refresh_token(RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=_ts(_REFRESH_TTL_S),
        ))
        self._store.delete_auth_code(authorization_code.code)
        logger.info("OAuth: issued tokens for client=%s scopes=%s", client.client_id, scopes)

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=_ACCESS_TTL_S,
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )

    # ── refresh token flow ────────────────────────────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        rt = self._store.get_refresh_token(refresh_token)
        if rt and rt.client_id != client.client_id:
            logger.warning("OAuth: refresh token client mismatch — rejecting")
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        scopes = scopes or refresh_token.scopes
        new_access = secrets.token_urlsafe(32)

        self._store.save_access_token(AccessToken(
            token=new_access,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=_ts(_ACCESS_TTL_S),
        ))
        # Refresh token is intentionally NOT rotated.
        # mcp-remote (and other clients) open parallel connections that all try
        # to exchange the same refresh token concurrently. Rotating on the first
        # exchange invalidates the token for all concurrent requests → they fall
        # back to browser auth flow → repeated consent popups.
        # The refresh token expires naturally after 30 days via Redis TTL.
        logger.info("OAuth: issued new access token for client=%s", client.client_id)

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=_ACCESS_TTL_S,
            scope=" ".join(scopes),
            refresh_token=refresh_token.token,  # same refresh token returned
        )

    # ── token verification (called on every /mcp request) ────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self._store.get_access_token(token)

    # ── revocation ────────────────────────────────────────────────────────────

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._store.delete_access_token(token.token)
        else:
            self._store.delete_refresh_token(token.token)
        logger.info("OAuth: revoked token for client=%s", token.client_id)
