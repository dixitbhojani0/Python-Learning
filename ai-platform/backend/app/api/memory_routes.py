"""
backend/app/api/memory_routes.py

Consent toggle + transparency/deletion endpoints (§14, §19 data
minimization/export-deletion). A user must be able to see and remove what's
remembered about them, not just turn the feature off going forward.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import Principal, get_current_principal, get_scoped_session
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import User, UserMemory

router = APIRouter(prefix="/v1/me", tags=["memory"])


class ConsentRequest(BaseModel):
    enabled: bool


class MemoryItemOut(BaseModel):
    key: str
    value: str
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.post("/memory-consent")
async def set_memory_consent(
    body: ConsentRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
) -> dict:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user.memory_consent = body.enabled
    return {"memory_consent": user.memory_consent}


@router.get("/memory", response_model=list[MemoryItemOut])
async def list_my_memory(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[MemoryItemOut]:
    rows = (
        await session.execute(select(UserMemory).where(UserMemory.user_id == principal.user_id).order_by(UserMemory.key))
    ).scalars().all()
    return [MemoryItemOut.model_validate(r) for r in rows]


@router.delete("/memory/{key}")
async def delete_my_memory_item(
    key: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    result = await session.execute(
        delete(UserMemory).where(UserMemory.user_id == principal.user_id, UserMemory.key == key)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("memory.item_not_found", locale=locale))
    return {"deleted": key}
