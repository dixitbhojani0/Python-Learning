"""
backend/app/api/tenant_routes.py

Two routes whose entire purpose is proving §J's tenant-isolation guarantee
end-to-end, not just business value:
  GET /v1/users/me                — any authenticated user, own record only
  GET /v1/tenants/{tenant_id}/users — requires "users:read"; the path param
        is checked against the caller's own tenant (404 on mismatch, not
        403 — never confirm that another tenant's resource exists), AND the
        query itself runs through the RLS-scoped session, so even if the
        app-level check were ever removed by a future bug, the database
        still cannot return another tenant's rows.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.deps import Principal, get_current_principal, get_scoped_session, require_permission
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import User

router = APIRouter(tags=["tenancy"])


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str

    model_config = {"from_attributes": True}


@router.get("/v1/users/me", response_model=UserOut)
async def get_me(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
) -> UserOut:
    user = await session.get(User, principal.user_id)
    # Cannot happen under normal operation (a valid token implies the user
    # existed at issuance) — but if the account was deleted after the token
    # was issued, fail closed rather than return a null-filled response.
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return UserOut(id=user.id, email=user.email, role=principal.role)


@router.get("/v1/tenants/{tenant_id}/users", response_model=list[UserOut])
async def list_tenant_users(
    tenant_id: uuid.UUID,
    principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> list[UserOut]:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    if tenant_id != principal.tenant_id:
        # 404, not 403 — a 403 would confirm the other tenant's ID is a real
        # resource; 404 gives an attacker no signal either way (§V auth table).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("auth.unauthorized", locale=locale))

    users = (await session.execute(select(User).options(selectinload(User.role)))).scalars().all()
    return [UserOut(id=u.id, email=u.email, role=u.role.name) for u in users]
