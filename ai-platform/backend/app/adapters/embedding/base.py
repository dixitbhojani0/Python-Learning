"""
backend/app/adapters/embedding/base.py

Same contract shape as adapters/llm/base.py — a batch embed() call (not
one-text-at-a-time) because every real provider bills/rate-limits per
request, not per text, and ingesting a document means embedding many
chunks at once.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, same order."""
        ...

    @abstractmethod
    def get_dimensions(self) -> int:
        """
        Vector width this provider produces. The `chunks.embedding` column
        (Phase 5 migration) is a fixed pgvector width — switching to a
        provider with a different dimension needs a migration + full
        re-embed, not just a config change. Named here so that mismatch is
        at least checkable, not a silent runtime shape error.
        """
        ...
