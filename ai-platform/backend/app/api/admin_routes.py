"""
backend/app/api/admin_routes.py

Phase 9: the first operator-facing surface in the whole platform — every
capability built so far (providers, RAG corpus, memory, tenancy) had zero
admin/control-plane surface before this, only the end-user chat UI (§P).

Deliberately NOT gated behind a new "admin" role/permission namespace of its
own: reuses "users:read" (viewing tenant-wide operational data) and adds
"documents:read"/"documents:write" (document corpus management) — the same
flat permission-string model as every other route (§30: no speculative RBAC
graph ahead of a second real use needing it).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.adapters.embedding.registry import EmbeddingRegistry
from backend.app.adapters.llm.registry import LLMRegistry
from backend.app.api.deps import Principal, get_scoped_session, require_permission
from backend.app.core.config import resolve_config
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import Chunk, Document, Tenant, TelemetryEvent

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_PLATFORM_DEFAULTS = {"default_locale": DEFAULT_LOCALE, "llm_provider": "mock", "embedding_provider": "mock"}


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    compliance_profile: str

    model_config = {"from_attributes": True}


class ProviderStatusOut(BaseModel):
    llm_provider: str
    embedding_provider: str
    available_llm_providers: list[str]
    available_embedding_providers: list[str]


class DocumentSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    chunk_count: int


class TelemetrySummaryOut(BaseModel):
    total_chat_turns: int
    rag_usage_rate: float
    memory_usage_rate: float
    avg_latency_ms: float
    avg_response_word_count: float


@router.get("/tenant", response_model=TenantOut)
async def get_tenant(
    principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> TenantOut:
    # No RLS policy on `tenants` itself (it's the top of the hierarchy, §J) —
    # a tenant-scoped session can still read its own row here without issue.
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return TenantOut.model_validate(tenant)


@router.get("/providers", response_model=ProviderStatusOut)
async def get_provider_status(
    _principal: Principal = Depends(require_permission("users:read")),  # gates access; value itself unused
) -> ProviderStatusOut:
    config = resolve_config(_PLATFORM_DEFAULTS)
    return ProviderStatusOut(
        llm_provider=config.llm_provider,
        embedding_provider=config.embedding_provider,
        available_llm_providers=LLMRegistry.available(),
        available_embedding_providers=EmbeddingRegistry.available(),
    )


@router.get("/documents", response_model=list[DocumentSummaryOut])
async def list_documents(
    _principal: Principal = Depends(require_permission("documents:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[DocumentSummaryOut]:
    rows = (
        await session.execute(
            select(Document, func.count(Chunk.id))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [DocumentSummaryOut(id=doc.id, title=doc.title, chunk_count=count) for doc, count in rows]


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    _principal: Principal = Depends(require_permission("documents:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.document_not_found", locale=locale))

    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await session.delete(document)
    return {"deleted": str(document_id)}


@router.get("/telemetry", response_model=TelemetrySummaryOut)
async def get_telemetry_summary(
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> TelemetrySummaryOut:
    # Aggregated in Python, not SQL, deliberately: at this platform's dev/test
    # scale (§ audit table) a few hundred rows is nothing, and JSONB-aggregate
    # SQL would be real complexity for no measurable benefit yet — the seam to
    # revisit is the same one named for pgvector→Qdrant: a real bottleneck, not now.
    rows = (
        await session.execute(
            select(TelemetryEvent)
            .where(TelemetryEvent.event_type == "chat_turn")
            .order_by(TelemetryEvent.created_at.desc())
            .limit(200)
        )
    ).scalars().all()

    total = len(rows)
    if total == 0:
        return TelemetrySummaryOut(
            total_chat_turns=0, rag_usage_rate=0.0, memory_usage_rate=0.0, avg_latency_ms=0.0, avg_response_word_count=0.0
        )

    rag_count = sum(1 for r in rows if r.payload.get("rag_used"))
    memory_count = sum(1 for r in rows if r.payload.get("memory_used"))
    avg_latency = sum(r.payload.get("latency_ms", 0) for r in rows) / total
    avg_words = sum(r.payload.get("response_word_count", 0) for r in rows) / total

    return TelemetrySummaryOut(
        total_chat_turns=total,
        rag_usage_rate=round(rag_count / total, 3),
        memory_usage_rate=round(memory_count / total, 3),
        avg_latency_ms=round(avg_latency, 1),
        avg_response_word_count=round(avg_words, 1),
    )
