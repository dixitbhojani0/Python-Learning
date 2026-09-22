"""
backend/app/adapters/llm/providers/mock_provider.py

Deterministic test double — lets the whole platform run end-to-end (config,
routing, API responses) with zero external API keys. This is what §T+'s
free-tier deployment map runs against before a real Gemini/Groq key is wired
in, and what the test suite uses so tests never depend on network access.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from backend.app.adapters.llm.base import BaseLLMProvider
from backend.app.adapters.llm.registry import LLMRegistry


class MockProvider(BaseLLMProvider):
    """Echoes the prompt back with a fixed prefix — deterministic, no network call."""

    _MODEL_NAME = "mock-echo-v1"

    def _prefix(self, system: str) -> str:
        # Echoing `system` (when present) is deliberate, not incidental: it's
        # what lets RAG-augmentation tests (test_chat_api.py) verify a
        # retrieved context block actually reached the provider, without
        # needing a real LLM to "notice" it was given context.
        base = f"[mock:{self._MODEL_NAME}]"
        return f"{base} (system: {system})" if system else base

    async def generate_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        return f"{self._prefix(system)} {prompt}"

    async def stream_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> AsyncIterator[str]:
        # Word-by-word, no artificial delay — deterministic and fast for tests,
        # while still exercising the same multi-chunk path a real streaming
        # provider would (the streaming endpoint must not assume one chunk).
        for word in f"{self._prefix(system)} {prompt}".split(" "):
            yield word + " "

    def get_model_name(self) -> str:
        return self._MODEL_NAME


LLMRegistry.register("mock", MockProvider)
