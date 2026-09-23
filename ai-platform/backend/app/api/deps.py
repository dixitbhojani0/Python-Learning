"""
backend/app/api/deps.py

FastAPI dependencies for auth, RBAC, and RLS-scoped DB access. Every
protected route depends on `get_current_principal` (or `require_permission`)
for identity/authorization, and `get_scoped_session` for data access — never
on `tenant_scoped_session` directly, so it's structurally impossible for a
route to touch the DB without a resolved tenant context.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import PLATFORM_DEFAULTS, PlatformConfig, resolve_config
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.core.security import InvalidTokenError, decode_access_token
from backend.app.db.models import Tenant
from backend.app.db.session import tenant_scoped_session


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    permissions: frozenset[str]


def _unauthorized(locale: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=t("auth.unauthorized", locale=locale))


def get_current_principal(
    authorization: str | None = Header(default=None),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> Principal:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized(locale)

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        raise _unauthorized(locale) from None

    return Principal(
        user_id=uuid.UUID(payload["sub"]),
        tenant_id=uuid.UUID(payload["tenant_id"]),
        role=payload["role"],
        permissions=frozenset(payload["permissions"]),
    )


def require_permission(permission: str):
    """Dependency factory: `Depends(require_permission("users:read"))`."""

    def _check(
        principal: Principal = Depends(get_current_principal),
        accept_language: str | None = Header(default=None, alias="Accept-Language"),
    ) -> Principal:
        locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]
        if permission not in principal.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=t("auth.forbidden", locale=locale, permission=permission),
            )
        return principal

    return _check


async def get_scoped_session(principal: Principal = Depends(get_current_principal)) -> AsyncIterator[AsyncSession]:
    """The only way a route should reach the DB — ties every query to the caller's tenant."""
    async with tenant_scoped_session(principal.tenant_id) as session:
        yield session


async def get_tenant_config(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
) -> PlatformConfig:
    """
    Resolves config with the tenant layer actually wired in (§7) — every call
    site used to call resolve_config(PLATFORM_DEFAULTS) alone, so a tenant's
    config_overrides never took effect no matter what an admin saved. This is
    the one place that changes, not every caller (the whole point of a single
    resolver seam).
    """
    tenant = await session.get(Tenant, principal.tenant_id)
    overrides = tenant.config_overrides if tenant is not None else {}
    return resolve_config(PLATFORM_DEFAULTS, overrides)
