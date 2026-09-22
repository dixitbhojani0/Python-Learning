"""
backend/app/rag/ingestion.py

Document -> chunks -> embeddings -> stored rows. A plain function, not a
"service" class — there's no state to hold between calls, so a class would
just be an unnecessary place to put what's really one linear operation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.adapters.embedding.registry import EmbeddingRegistry
from backend.app.db.models import Chunk, Document
from backend.app.rag.chunking import chunk_text


@dataclass
class IngestResult:
    document: Document
    chunk_count: int


async def ingest_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    content: str,
    embedding_provider: str = "mock",
) -> IngestResult:
    """
    `session` must already be a tenant_scoped_session (§J) — this function
    does not open its own; it participates in whatever transaction the
    caller is managing, same discipline as chat_routes.py.
    """
    document = Document(id=uuid.uuid4(), tenant_id=tenant_id, title=title, content=content)
    session.add(document)
    await session.flush()

    pieces = chunk_text(content)
    if pieces:
        provider = EmbeddingRegistry.create(embedding_provider)
        vectors = await provider.embed(pieces)
        for index, (piece, vector) in enumerate(zip(pieces, vectors)):
            session.add(
                Chunk(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    document_id=document.id,
                    chunk_index=index,
                    content=piece,
                    embedding=vector,
                )
            )

    return IngestResult(document=document, chunk_count=len(pieces))
