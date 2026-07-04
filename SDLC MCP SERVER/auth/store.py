"""
auth/store.py

Redis-backed OAuth 2.1 token store.

Redis is the correct backend for token storage:
  - TTL enforced natively per key — no manual purge needed
  - Atomic operations — no threading.Lock needed
  - Tokens never touch the filesystem as plain text
  - Survives server restarts automatically

Key schema (all under the "mcp:" namespace):
    mcp:oauth:client:{client_id}  → JSON   no TTL (clients persist until revoked)
    mcp:oauth:pending:{req_id}    → JSON   EX 600s  (10-min consent window)
    mcp:oauth:code:{code}         → JSON   EX 300s  (5-min auth code lifetime per RFC 6749)
    mcp:oauth:token:{token}       → JSON   EX = seconds until expires_at
    mcp:oauth:refresh:{token}     → JSON   EX = seconds until expires_at
"""
import json
import logging
import time

import redis
from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull

logger = logging.getLogger(__name__)

_PFX_CLIENT  = "mcp:oauth:client:{}"
_PFX_PENDING = "mcp:oauth:pending:{}"
_PFX_CODE    = "mcp:oauth:code:{}"
_PFX_TOKEN   = "mcp:oauth:token:{}"
_PFX_REFRESH = "mcp:oauth:refresh:{}"

_PENDING_TTL = 600   # 10 min — time to complete browser consent
_CODE_TTL    = 300   # 5 min  — RFC 6749 §4.1.2 recommendation


def _ttl_from_expires_at(expires_at: int | float | None) -> int | None:
    """Convert an absolute expiry timestamp to a Redis TTL in seconds."""
    if expires_at is None:
        return None
    return max(1, int(expires_at - time.time()))


class TokenStore:
    def __init__(self, redis_url: str = "redis://127.0.0.1:6379") -> None:
        self._r = redis.from_url(redis_url, decode_responses=True)
        try:
            self._r.ping()
            logger.info("TokenStore: connected to Redis at %s", redis_url)
        except redis.ConnectionError as exc:
            logger.error("TokenStore: cannot connect to Redis at %s — %s", redis_url, exc)
            raise

    # ── internal helpers ──────────────────────────────────────────────────────

    def _get(self, key: str) -> dict | None:
        raw = self._r.get(key)
        return json.loads(raw) if raw else None

    def _set(self, key: str, value: dict, ex: int | None = None) -> None:
        self._r.set(key, json.dumps(value), ex=ex)

    # ── clients ───────────────────────────────────────────────────────────────

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self._get(_PFX_CLIENT.format(client_id))
        return OAuthClientInformationFull(**raw) if raw else None

    def save_client(self, client: OAuthClientInformationFull) -> None:
        self._set(_PFX_CLIENT.format(client.client_id), client.model_dump(mode="json"))

    # ── pending auth requests ─────────────────────────────────────────────────
    # Secondary index mcp:oauth:pending:client:{client_id} → req_id lets
    # authorize() detect an in-progress consent for the same client and return
    # the same consent URL instead of opening a new browser tab.

    _PFX_PENDING_CLIENT = "mcp:oauth:pending:client:{}"

    def save_pending_auth(self, req_id: str, data: dict) -> None:
        pipe = self._r.pipeline()
        pipe.set(_PFX_PENDING.format(req_id), json.dumps(data), ex=_PENDING_TTL)
        pipe.set(self._PFX_PENDING_CLIENT.format(data["client_id"]), req_id, ex=_PENDING_TTL)
        pipe.execute()

    def find_pending_req_by_client(self, client_id: str) -> str | None:
        """Return an in-progress req_id for this client, or None."""
        return self._r.get(self._PFX_PENDING_CLIENT.format(client_id))

    def get_pending_auth(self, req_id: str) -> dict | None:
        """Read without deleting — used by the GET consent handler."""
        raw = self._r.get(_PFX_PENDING.format(req_id))
        return json.loads(raw) if raw else None

    def pop_pending_auth(self, req_id: str) -> dict | None:
        """Atomically read-and-delete the pending auth entry + client index."""
        key = _PFX_PENDING.format(req_id)
        raw = self._r.getdel(key)
        if not raw:
            return None
        data = json.loads(raw)
        self._r.delete(self._PFX_PENDING_CLIENT.format(data.get("client_id", "")))
        return data

    # ── authorization codes ───────────────────────────────────────────────────

    def save_auth_code(self, code: AuthorizationCode) -> None:
        self._set(_PFX_CODE.format(code.code), code.model_dump(mode="json"), ex=_CODE_TTL)

    def get_auth_code(self, code: str) -> AuthorizationCode | None:
        raw = self._get(_PFX_CODE.format(code))
        return AuthorizationCode(**raw) if raw else None

    def delete_auth_code(self, code: str) -> None:
        self._r.delete(_PFX_CODE.format(code))

    # ── access tokens ─────────────────────────────────────────────────────────

    def save_access_token(self, token: AccessToken) -> None:
        ex = _ttl_from_expires_at(token.expires_at)
        self._set(_PFX_TOKEN.format(token.token), token.model_dump(mode="json"), ex=ex)

    def get_access_token(self, token: str) -> AccessToken | None:
        raw = self._get(_PFX_TOKEN.format(token))
        return AccessToken(**raw) if raw else None

    def delete_access_token(self, token: str) -> None:
        self._r.delete(_PFX_TOKEN.format(token))

    # ── refresh tokens ────────────────────────────────────────────────────────

    def save_refresh_token(self, token: RefreshToken) -> None:
        ex = _ttl_from_expires_at(token.expires_at)
        self._set(_PFX_REFRESH.format(token.token), token.model_dump(mode="json"), ex=ex)

    def get_refresh_token(self, token: str) -> RefreshToken | None:
        raw = self._get(_PFX_REFRESH.format(token))
        return RefreshToken(**raw) if raw else None

    def delete_refresh_token(self, token: str) -> None:
        self._r.delete(_PFX_REFRESH.format(token))
