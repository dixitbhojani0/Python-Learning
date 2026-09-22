from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.tools.base import BaseTool
from backend.app.tools.registry import ToolRegistry


class CurrentTimeTool(BaseTool):
    name = "current_time"
    description = "Returns the current UTC time."
    risk_level = "low"

    async def execute(self, *, session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, **kwargs: object) -> dict:
        return {"utc_time": datetime.now(timezone.utc).isoformat()}


ToolRegistry.register("current_time", CurrentTimeTool)
