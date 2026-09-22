"""
backend/app/adapters/embedding/providers/mock_embedding_provider.py

Deterministic, hash-based, zero network — same role as the LLM mock
provider: the test suite and a zero-key dev environment run the whole RAG
pipeline (ingest -> embed -> store -> retrieve) end-to-end without hitting
a real embedding API.

ponytail: this is NOT a semantic embedding — it has no notion that "dog"
and "puppy" are related. It only guarantees identical text -> identical
vector and different text -> a different vector. That's sufficient to
prove the pipeline's plumbing (storage, indexing, nearest-neighbor query)
end-to-end; retrieval-quality tests need a real semantic provider
(gemini_embedding_provider.py) once a key is available — do not mistake a
green test here for "retrieval quality verified."
"""
from __future__ import annotations

import hashlib

from backend.app.adapters.embedding.base import BaseEmbeddingProvider
from backend.app.adapters.embedding.registry import EmbeddingRegistry

DIMENSIONS = 768


def _embed_one(text: str) -> list[float]:
    values: list[float] = []
    block = 0
    while len(values) < DIMENSIONS:
        digest = hashlib.sha256(text.encode("utf-8") + block.to_bytes(4, "big")).digest()
        values.extend((b / 255.0) * 2 - 1 for b in digest)  # map byte [0,255] -> float [-1,1]
        block += 1
    values = values[:DIMENSIONS]

    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values] if norm else values


class MockEmbeddingProvider(BaseEmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_embed_one(text) for text in texts]

    def get_dimensions(self) -> int:
        return DIMENSIONS


EmbeddingRegistry.register("mock", MockEmbeddingProvider)
