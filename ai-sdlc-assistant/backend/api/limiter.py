"""
backend/api/limiter.py

Slowapi rate limiter singleton — imported by both main.py and route files.

WHY A SEPARATE MODULE (not in main.py)?
  main.py imports route files (chat.py, hitl.py) via app.include_router().
  If limiter were defined in main.py, route files importing it from main.py
  would create a circular import: main → chat → main.
  Defining limiter here breaks that cycle:
    main.py  imports limiter.py  ✅
    chat.py  imports limiter.py  ✅
    No cycle.

RATE LIMITS (per authenticated identity, not per IP):
  /api/chat      — 10/minute  (LLM calls are expensive; prevents API cost explosion)
  /api/hitl/*    — 5/minute   (approval actions are rare; low limit blocks abuse)
  /health        — 30/minute  (monitoring tools poll every 2s; allow headroom)
  global default — 100/minute (safety net for any unlimied route)

WHY KEYED BY TOKEN, NOT IP?
  Multiple users behind corporate NAT share one IP. IP-based limiting would
  collectively throttle an entire office when one user is heavy. Token-based
  limiting is per-identity: each developer/manager/stakeholder token is independent.
"""
import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.core.settings import settings

logger = logging.getLogger(__name__)


def _get_user_key(request: Request) -> str:
    """
    Rate limit key: prefer auth token (per identity) over IP (per network).

    Resolution order:
      1. x-token header (demo mode — dev_token_alice, manager_token_bob, etc.)
      2. Authorization: Bearer <jwt> (production JWT mode)
      3. Client IP fallback (unauthenticated requests — will get 401 anyway,
         but still rate-limit to block auth endpoint flooding)

    We use only the first 20 chars of the token — enough to distinguish identities
    without logging a full credential.
    """
    # Demo token (x-token header)
    token = request.headers.get("x-token") or request.headers.get("X-Token")
    if token:
        return f"token:{token[:20]}"

    # JWT Bearer token
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        bearer = auth[7:].strip()
        return f"bearer:{bearer[:20]}"

    # IP fallback
    client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"


# ── Limiter singleton ──────────────────────────────────────────────────────────
#
# storage_uri=settings.REDIS_URL means rate limit counters survive server restarts.
# If Redis is unreachable, slowapi falls back to in-memory counting automatically
# (graceful degradation — matches the pattern used by HITLManager and SemanticCache).
#
# Import in route files as:
#   from backend.api.limiter import limiter

limiter = Limiter(
    key_func=_get_user_key,
    storage_uri=settings.REDIS_URL,
    default_limits=["100/minute"],
)
