"""
backend/app/api/tool_approval_routes.py

The human half of §15's HITL gate: an operator sees what a high-risk tool
call wants to do (list), and explicitly approves (actually executes it,
now, for real) or rejects it (never executes). A single "tools:approve"
permission gates this whole surface — seeing what's pending is as
sensitive as acting on it, so there's no separate read-only tier.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import Principal, get_scoped_session, require_permission
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import PendingToolApproval
from backend.app.observability.telemetry import record_event
from backend.app.tools import builtin as _tool_builtins  # noqa: F401  (triggers registration)
from backend.app.tools.base import ToolExecutionError
from backend.app.tools.registry import ToolRegistry

router = APIRouter(prefix="/v1/admin/tool-approvals", tags=["tools"])


class PendingApprovalOut(BaseModel):
    id: uuid.UUID
    tool_name: str
    tool_args: dict
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


async def _get_pending_approval(
    session: AsyncSession, approval_id: uuid.UUID, locale: str
) -> PendingToolApproval:
    approval = await session.get(PendingToolApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("tools.approval_not_found", locale=locale))
    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=t("tools.approval_already_resolved", locale=locale)
        )
    return approval


@router.get("", response_model=list[PendingApprovalOut])
async def list_pending_approvals(
    _principal: Principal = Depends(require_permission("tools:approve")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[PendingApprovalOut]:
    rows = (
        await session.execute(
            select(PendingToolApproval)
            .where(PendingToolApproval.status == "pending")
            .order_by(PendingToolApproval.created_at.desc())
        )
    ).scalars().all()
    return [PendingApprovalOut.model_validate(r) for r in rows]


@router.post("/{approval_id}/approve")
async def approve_tool_call(
    approval_id: uuid.UUID,
    principal: Principal = Depends(require_permission("tools:approve")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]
    approval = await _get_pending_approval(session, approval_id, locale)

    tool = ToolRegistry.create(approval.tool_name)
    try:
        result = await tool.execute(
            session=session, tenant_id=principal.tenant_id, user_id=approval.user_id, **approval.tool_args
        )
        success = True
    except ToolExecutionError as exc:
        result = {"error": str(exc)}
        success = False

    approval.status = "approved"
    approval.result = result
    approval.resolved_by = principal.user_id
    approval.resolved_at = datetime.now().astimezone()

    await record_event(
        session,
        tenant_id=principal.tenant_id,
        event_type="tool_call_approved",
        payload={"tool_name": approval.tool_name, "approval_id": str(approval_id), "success": success},
    )

    return {"status": "approved", "result": result}


@router.post("/{approval_id}/reject")
async def reject_tool_call(
    approval_id: uuid.UUID,
    principal: Principal = Depends(require_permission("tools:approve")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]
    approval = await _get_pending_approval(session, approval_id, locale)

    approval.status = "rejected"
    approval.resolved_by = principal.user_id
    approval.resolved_at = datetime.now().astimezone()

    await record_event(
        session,
        tenant_id=principal.tenant_id,
        event_type="tool_call_rejected",
        payload={"tool_name": approval.tool_name, "approval_id": str(approval_id)},
    )

    return {"status": "rejected"}
