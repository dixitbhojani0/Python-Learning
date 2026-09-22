"""
backend/app/tools/builtin/delete_all_documents_tool.py

The one high-risk tool in this platform — deliberately picked because it is
genuinely destructive (wipes the tenant's whole RAG corpus) and easy to
reason about, to demonstrate the HITL gate (§15) with a real consequence
behind it, not a toy example with nothing at stake.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Chunk, Document
from backend.app.tools.base import BaseTool
from backend.app.tools.registry import ToolRegistry


class DeleteAllDocumentsTool(BaseTool):
    name = "delete_all_documents"
    description = "Permanently deletes every ingested document in the tenant's knowledge base."
    risk_level = "high"

    async def execute(self, *, session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, **kwargs: object) -> dict:
        count = (await session.execute(select(func.count()).select_from(Document))).scalar_one()
        await session.execute(delete(Chunk))
        await session.execute(delete(Document))
        return {"deleted_document_count": count}


ToolRegistry.register("delete_all_documents", DeleteAllDocumentsTool)
