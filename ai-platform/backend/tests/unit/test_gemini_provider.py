"""
Gemini provider tests — all HTTP calls mocked via httpx.MockTransport, never
a real network call (same discipline as the rest of this suite: tests must
run with zero external dependencies or API keys).
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.app.adapters.llm.base import ProviderMisconfiguredError
from backend.app.adapters.llm.providers.gemini_provider import GeminiProvider
from backend.app.core.settings import settings


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://generativelanguage.googleapis.com")


def _gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-test-key")


# ── Positive ──────────────────────────────────────────────────────────────

async def test_generate_text_extracts_the_response_text(gemini_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "fake-test-key"
        return httpx.Response(200, json=_gemini_response("hello from gemini"))

    provider = GeminiProvider(client=_client_with(handler))
    result = await provider.generate_text("hi")

    assert result == "hello from gemini"


async def test_stream_text_yields_each_chunk(gemini_key):
    body = (
        f"data: {json.dumps(_gemini_response('hel'))}\n\n"
        f"data: {json.dumps(_gemini_response('lo'))}\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode())

    provider = GeminiProvider(client=_client_with(handler))
    chunks = [c async for c in provider.stream_text("hi")]

    assert chunks == ["hel", "lo"]


# ── Negative ──────────────────────────────────────────────────────────────

def test_missing_api_key_raises_at_construction_before_any_network_call(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    with pytest.raises(ProviderMisconfiguredError):
        GeminiProvider()


async def test_http_error_status_raises_not_silently_returns_empty(gemini_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    provider = GeminiProvider(client=_client_with(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate_text("hi")


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_response_with_no_candidates_returns_empty_string_not_a_crash(gemini_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    provider = GeminiProvider(client=_client_with(handler))
    result = await provider.generate_text("hi")

    assert result == ""


# ── Side effects ────────────────────────────────────────────────────────

async def test_model_name_reflects_settings_not_hardcoded(gemini_key, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-custom-model")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "gemini-custom-model" in str(request.url)
        return httpx.Response(200, json=_gemini_response("ok"))

    provider = GeminiProvider(client=_client_with(handler))
    assert provider.get_model_name() == "gemini-custom-model"
    await provider.generate_text("hi")  # asserts inside handler that the URL used the configured model
