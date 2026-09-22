"""
backend/app/core/security.py

Password hashing + JWT issuance/verification. This is the seam an external
OIDC provider replaces later (§M's IdentityProviderAdapter) — the rest of
the app depends on `decode_access_token()` returning a Principal-shaped
payload, not on how that payload was produced. Swapping in real OIDC means
changing the login route, not every place a token is checked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from backend.app.core.settings import settings


class InvalidTokenError(Exception):
    """Raised for any token that fails signature, expiry, or shape validation."""


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str, permissions: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "permissions": permissions,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
