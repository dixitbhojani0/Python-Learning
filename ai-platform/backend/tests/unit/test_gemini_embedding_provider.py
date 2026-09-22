from __future__ import annotations

import httpx
import pytest

from backend.app.adapters.llm.base import ProviderMisconfiguredError
from backend.app.adapters.embedding.providers.gemini_embedding_provider import GeminiEmbeddingProvider
from backend.app.core.settings import settings


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://generativelanguage.googleapis.com")


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-test-key")


# ── Positive ──────────────────────────────────────────────────────────────

async def test_embed_extracts_values_for_each_input_in_order(gemini_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "fake-test-key"
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]})

    provider = GeminiEmbeddingProvider(client=_client_with(handler))
    result = await provider.embed(["a", "b"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]


def test_dimensions_is_768_matching_the_chunks_table_column(gemini_key):
    provider = GeminiEmbeddingProvider(client=_client_with(lambda request: httpx.Response(200, json={})))
    assert provider.get_dimensions() == 768


# ── Negative ──────────────────────────────────────────────────────────────

def test_missing_api_key_raises_at_construction(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    with pytest.raises(ProviderMisconfiguredError):
        GeminiEmbeddingProvider()


async def test_http_error_status_raises(gemini_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    provider = GeminiEmbeddingProvider(client=_client_with(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["a"])


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_single_text_batch_of_one_still_uses_the_batch_endpoint(gemini_key):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"embeddings": [{"values": [0.5]}]})

    provider = GeminiEmbeddingProvider(client=_client_with(handler))
    await provider.embed(["only one"])

    assert "batchEmbedContents" in captured["path"]
