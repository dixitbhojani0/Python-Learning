"""
backend/auth/middleware.py

Dual-mode authentication:

  Production mode (JWT):
    Header: Authorization: Bearer <jwt>
    The JWT is validated using HS256 with JWT_SECRET_KEY from settings.
    Required claims: sub (user_id), role, name, project.
    Active when JWT_SECRET_KEY is set to something other than "placeholder".

  Development / demo mode (static tokens):
    Header: x-token: dev_token_alice
    Simple dict lookup against DEMO_TOKEN_* from settings.
    Active when JWT_SECRET_KEY is "placeholder" OR APP_ENV == "development".
    Both headers are accepted simultaneously so the frontend doesn't need to change.

The UserContext dataclass is identical in both modes — all downstream code is unchanged.
"""
import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from backend.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """Represents the authenticated user for one request."""
    token:   str
    role:    str     # developer | manager | technical_leader | stakeholder | admin
    name:    str
    project: str


# ── Demo user registry ────────────────────────────────────────────────────────
# Used in development / when JWT is not configured.

_DEMO_USERS: dict[str, UserContext] = {
    settings.DEMO_TOKEN_DEVELOPER: UserContext(
        token=settings.DEMO_TOKEN_DEVELOPER,
        role="developer",
        name="Alice (Developer)",
        project=settings.DEFAULT_PROJECT,
    ),
    settings.DEMO_TOKEN_MANAGER: UserContext(
        token=settings.DEMO_TOKEN_MANAGER,
        role="manager",
        name="Bob (Project Manager)",
        project=settings.DEFAULT_PROJECT,
    ),
    settings.DEMO_TOKEN_STAKEHOLDER: UserContext(
        token=settings.DEMO_TOKEN_STAKEHOLDER,
        role="stakeholder",
        name="Client (Stakeholder)",
        project=settings.DEFAULT_PROJECT,
    ),
}

_JWT_ENABLED = (
    settings.JWT_SECRET_KEY not in ("placeholder", "", "change_this_in_production")
    and settings.APP_ENV == "production"
)


# ── JWT validation ────────────────────────────────────────────────────────────

def _decode_jwt(token: str) -> dict:
    """
    Decode and validate a JWT using HS256 + JWT_SECRET_KEY.

    Returns the claims dict on success.
    Raises HTTPException(401) on any validation failure.

    Uses PyJWT (pip install PyJWT). Falls back gracefully if not installed.
    """
    try:
        import jwt as pyjwt
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="PyJWT not installed. Run: pip install PyJWT",
        )
    try:
        payload = pyjwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["sub", "role", "exp"]},
        )
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired.")
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


def _user_from_jwt(token: str) -> UserContext:
    """Decode JWT and build a UserContext from its claims."""
    claims = _decode_jwt(token)
    role = claims.get("role", "developer")
    if role not in ("developer", "manager", "technical_leader", "stakeholder", "admin"):
        role = "developer"
    return UserContext(
        token=token,
        role=role,
        name=claims.get("name") or claims.get("email") or claims.get("sub", "user"),
        project=claims.get("project", settings.DEFAULT_PROJECT),
    )


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_current_user(request: Request) -> UserContext:
    """
    FastAPI dependency — validates credentials and returns UserContext.

    Accepts two header formats (tried in order):
      1. Authorization: Bearer <jwt>   — production JWT auth
      2. x-token: dev_token_alice      — demo token auth (development)

    Usage in routes:
        user: UserContext = Depends(get_current_user)
    """
    # ── Try JWT first (Authorization: Bearer header) ──────────────────────────
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
        if _JWT_ENABLED:
            user = _user_from_jwt(bearer_token)
            logger.debug("Auth(JWT): authenticated %s as %s", user.name, user.role)
            return user
        # JWT not enabled — fall through to demo token check
        logger.debug("Auth: Bearer header present but JWT not enabled — trying demo tokens")

    # ── Try demo token (x-token header) ──────────────────────────────────────
    x_token = request.headers.get("x-token") or request.headers.get("X-Token")
    if x_token:
        user = _DEMO_USERS.get(x_token)
        if user:
            logger.debug("Auth(demo): authenticated %s as %s", user.name, user.role)
            return user
        logger.warning("Auth: invalid demo token '%s...'", x_token[:20])

    # ── No valid credentials ──────────────────────────────────────────────────
    mode_hint = (
        "Use Authorization: Bearer <jwt>"
        if _JWT_ENABLED
        else "Use x-token: dev_token_alice (or manager_token_bob / stakeholder_token_client)"
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Authentication required. {mode_hint}",
    )


async def get_admin_user(request: Request) -> UserContext:
    """
    Admin-only dependency. Only manager / technical_leader / admin roles allowed.
    Usage:  user: UserContext = Depends(get_admin_user)
    """
    user = await get_current_user(request)
    if user.role not in ("manager", "technical_leader", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access requires manager, technical_leader, or admin role.",
        )
    return user
