"""
backend/app/api/rag_routes.py

Phase 5 slice: ingest a document, search chunks by semantic similarity.
Deliberately NOT wired into /v1/chat yet (retrieval-augmented generation
itself) — that's the natural next step, but this phase is "prove ingestion
and retrieval work," kept independently testable rather than bundled with
a chat-prompt-assembly change in the same commit.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.adapters.embedding import providers as _embedding_providers  # noqa: F401  (triggers registration)
from backend.app.api.deps import Principal, get_current_principal, get_scoped_session
from backend.app.core.config import resolve_config
from backend.app.core.i18n import DEFAULT_LOCALE
from backend.app.db.session import tenant_scoped_session
from backend.app.rag.ingestion import ingest_document
from backend.app.rag.retrieval import search_chunks

router = APIRouter(prefix="/v1", tags=["rag"])

_PLATFORM_DEFAULTS = {"default_locale": DEFAULT_LOCALE, "embedding_provider": "mock"}


class IngestRequest(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)


class IngestResponse(BaseModel):
    id: uuid.UUID
    title: str
    chunk_count: int


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    distance: float


@router.post("/documents", response_model=IngestResponse)
async def create_document(
    body: IngestRequest,
    principal: Principal = Depends(get_current_principal),
) -> IngestResponse:
    config = resolve_config(_PLATFORM_DEFAULTS)
    async with tenant_scoped_session(principal.tenant_id) as session:
        result = await ingest_document(
            session,
            tenant_id=principal.tenant_id,
            title=body.title,
            content=body.content,
            embedding_provider=config.embedding_provider,
        )
    return IngestResponse(id=result.document.id, title=result.document.title, chunk_count=result.chunk_count)


@router.get("/search", response_model=list[SearchResult])
async def search(
    q: str = Query(min_length=1),
    top_k: int = Query(default=5, gt=0, le=50),
    # get_scoped_session already depends on get_current_principal — this
    # route needs auth+tenant-scoping, not the principal's fields directly.
    session: AsyncSession = Depends(get_scoped_session),
) -> list[SearchResult]:
    config = resolve_config(_PLATFORM_DEFAULTS)
    results = await search_chunks(session, query=q, top_k=top_k, embedding_provider=config.embedding_provider)
    return [
        SearchResult(chunk_id=r.chunk.id, document_id=r.chunk.document_id, content=r.chunk.content, distance=r.distance)
        for r in results
    ]
