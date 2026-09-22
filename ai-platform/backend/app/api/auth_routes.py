"""
backend/app/api/auth_routes.py

Minimal login endpoint — the seam §M's IdentityProviderAdapter replaces with
real OIDC/SSO later. Deliberately NOT a full OIDC authorization-code flow:
that requires a chosen external IdP (§AA — a decision not yet made), and
building the whole redirect/callback dance against a hypothetical IdP would
be exactly the kind of speculative code the platform's own standards forbid.
This gives every later phase a real, working `Principal` to depend on now.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.core.security import create_access_token, verify_password
from backend.app.db.models import Tenant, User
from backend.app.db.session import get_db_session, tenant_scoped_session

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> LoginResponse:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    async with get_db_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == body.tenant_slug))).scalar_one_or_none()

    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=t("auth.tenant_not_found", locale=locale, slug=body.tenant_slug),
        )

    async with tenant_scoped_session(tenant.id) as session:
        user = (
            await session.execute(
                select(User).where(User.email == body.email).options(selectinload(User.role))
            )
        ).scalars().first()

        if user is None or not verify_password(body.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=t("auth.invalid_credentials", locale=locale),
            )

        # role relationship access must happen while the session is open
        role_name = user.role.name
        permissions = list(user.role.permissions)

    token = create_access_token(user_id=user.id, tenant_id=tenant.id, role=role_name, permissions=permissions)
    return LoginResponse(access_token=token)
