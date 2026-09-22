"""
Groq provider tests — mocked HTTP only, same discipline as test_gemini_provider.py.
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.app.adapters.llm.base import ProviderMisconfiguredError
from backend.app.adapters.llm.providers.groq_provider import GroqProvider
from backend.app.core.settings import settings


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.groq.com/openai/v1")


def _chat_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture
def groq_key(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "fake-test-key")


# ── Positive ──────────────────────────────────────────────────────────────

async def test_generate_text_extracts_the_response_content(groq_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fake-test-key"
        return httpx.Response(200, json=_chat_response("hello from groq"))

    provider = GroqProvider(client=_client_with(handler))
    result = await provider.generate_text("hi")

    assert result == "hello from groq"


async def test_stream_text_yields_delta_chunks_and_stops_at_done(groq_key):
    body = (
        f"data: {json.dumps({'choices': [{'delta': {'content': 'hel'}}]})}\n\n"
        f"data: {json.dumps({'choices': [{'delta': {'content': 'lo'}}]})}\n\n"
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode())

    provider = GroqProvider(client=_client_with(handler))
    chunks = [c async for c in provider.stream_text("hi")]

    assert chunks == ["hel", "lo"]


# ── Negative ──────────────────────────────────────────────────────────────

def test_missing_api_key_raises_at_construction_before_any_network_call(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    with pytest.raises(ProviderMisconfiguredError):
        GroqProvider()


async def test_http_error_status_raises(groq_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    provider = GroqProvider(client=_client_with(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate_text("hi")


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_delta_with_no_content_key_is_skipped_not_yielded_as_none(groq_key):
    body = (
        f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant'}}]})}\n\n"  # first chunk: role only, no content
        f"data: {json.dumps({'choices': [{'delta': {'content': 'hi'}}]})}\n\n"
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode())

    provider = GroqProvider(client=_client_with(handler))
    chunks = [c async for c in provider.stream_text("hi")]

    assert chunks == ["hi"]  # the role-only chunk produced no spurious empty/None entry


# ── Side effects ────────────────────────────────────────────────────────

async def test_system_prompt_is_included_only_when_provided(groq_key):
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response("ok"))

    provider = GroqProvider(client=_client_with(handler))
    await provider.generate_text("hi")
    await provider.generate_text("hi", system="be terse")

    assert captured[0]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured[1]["messages"] == [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}]
