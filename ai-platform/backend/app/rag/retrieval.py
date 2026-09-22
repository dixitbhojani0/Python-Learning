"""
backend/app/rag/retrieval.py

Cosine-distance nearest-neighbor search over `chunks`, scoped to the
caller's tenant by RLS (the session passed in must already be tenant-scoped
— same discipline as ingestion.py). No ANN index yet (see the Phase 5
migration's comment) — a sequential scan is exact and fine at dev/test scale.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.adapters.embedding.registry import EmbeddingRegistry
from backend.app.db.models import Chunk


@dataclass
class ScoredChunk:
    chunk: Chunk
    distance: float  # cosine distance — 0 is identical, larger is less similar


async def search_chunks(
    session: AsyncSession,
    *,
    query: str,
    top_k: int = 5,
    embedding_provider: str = "mock",
) -> list[ScoredChunk]:
    provider = EmbeddingRegistry.create(embedding_provider)
    [query_vector] = await provider.embed([query])

    distance = Chunk.embedding.cosine_distance(query_vector)
    rows = (
        await session.execute(select(Chunk, distance.label("distance")).order_by(distance).limit(top_k))
    ).all()

    return [ScoredChunk(chunk=row[0], distance=row[1]) for row in rows]
