"""
backend/app/adapters/embedding/providers/gemini_embedding_provider.py

text-embedding-004 (768 dimensions — matches the mock provider and the
chunks.embedding column width in the Phase 5 migration; see base.py's
docstring on why that width is a migration, not a config change, to alter).
Uses the batchEmbedContents endpoint so ingesting N chunks is one request,
not N.
"""
from __future__ import annotations

import httpx

from backend.app.adapters.embedding.base import BaseEmbeddingProvider
from backend.app.core.settings import settings

# Raised by base.py's ABC contract via ProviderMisconfiguredError, reused
# from the LLM adapter package rather than redefining an identical class —
# this is a genuine shared concern (missing API key), not the speculative
# shared-abstraction §30 warns against.
from backend.app.adapters.llm.base import ProviderMisconfiguredError
from backend.app.adapters.embedding.registry import EmbeddingRegistry

_BASE_URL = "https://generativelanguage.googleapis.com"
_MODEL = "models/text-embedding-004"
_DIMENSIONS = 768


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self, client: httpx.AsyncClient | None = None):
        if not settings.GEMINI_API_KEY:
            raise ProviderMisconfiguredError(
                "GEMINI_API_KEY is not set — add it to .env before selecting the 'gemini' embedding provider."
            )
        self._client = client or httpx.AsyncClient(base_url=_BASE_URL, timeout=30.0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.post(
            f"/v1beta/{_MODEL}:batchEmbedContents",
            params={"key": settings.GEMINI_API_KEY},
            json={
                "requests": [
                    {"model": _MODEL, "content": {"parts": [{"text": text}]}} for text in texts
                ]
            },
        )
        response.raise_for_status()
        return [item["values"] for item in response.json()["embeddings"]]

    def get_dimensions(self) -> int:
        return _DIMENSIONS


EmbeddingRegistry.register("gemini", GeminiEmbeddingProvider)
