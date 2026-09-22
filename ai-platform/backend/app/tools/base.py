"""
backend/app/tools/base.py

Every tool gets `session`/`tenant_id`/`user_id` on execute() even if it
never touches the DB — one consistent signature across all tools (same
reasoning as BaseLLMProvider's uniform method shape) beats a special case
for "tools that need the database" vs "tools that don't."

risk_level is not decoration: it is the ONE thing chat_routes.py's agent
loop reads to decide "run now" vs "create a PendingToolApproval and stop"
(§15 bounded autonomy — a tool is either safe enough to run unsupervised or
it is gated, there is no middle tier here).
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

RiskLevel = Literal["low", "high"]


class ToolExecutionError(Exception):
    """Raised by a tool's execute() for any expected failure (bad input, etc.) — never let a raw exception leak."""


class BaseTool(ABC):
    name: str
    description: str
    risk_level: RiskLevel

    @abstractmethod
    async def execute(self, *, session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, **kwargs: object) -> dict:
        ...
