"""
backend/auth/middleware.py

Simple role-based auth for demo.
Uses hardcoded demo tokens from .env — no OAuth complexity.

In production this would validate JWTs from Google OAuth.
The interface is identical — swap the implementation, keep the API.

Demo tokens (set in .env):
    dev_token_alice      → developer role
    manager_token_bob    → manager role
    stakeholder_token_client → stakeholder role
"""
import logging
from fastapi import Header, HTTPException, status
from dataclasses import dataclass

from backend.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """Represents the authenticated user for one request."""
    token: str
    role: str           # developer | manager | technical_leader | stakeholder
    name: str
    project: str


# ── Demo user registry — loaded from settings
# In production: validate JWT, extract claims
DEMO_USERS: dict[str, UserContext] = {
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


async def get_current_user(x_token: str = Header(..., alias="x-token")) -> UserContext:
    """
    FastAPI dependency — validates demo token and returns UserContext.
    Usage in routes:  user: UserContext = Depends(get_current_user)

    Frontend sends:   headers={"x-token": "dev_token_alice"}
    """
    user = DEMO_USERS.get(x_token)
    if not user:
        logger.warning("Auth: invalid token '%s'", x_token[:20])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token. Use one of the demo tokens from .env.example",
        )
    logger.debug("Auth: authenticated %s as %s", user.name, user.role)
    return user


async def get_admin_user(x_token: str = Header(..., alias="x-token")) -> UserContext:
    """
    Admin-only dependency. Only manager role can access /admin/* routes.
    Usage:  user: UserContext = Depends(get_admin_user)
    """
    user = await get_current_user(x_token)
    if user.role not in ("manager", "technical_leader"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access requires manager or technical_leader role",
        )
    return user
