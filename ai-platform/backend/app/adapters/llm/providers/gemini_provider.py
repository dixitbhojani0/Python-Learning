"""
backend/app/adapters/llm/providers/gemini_provider.py

Calls the Gemini REST API directly via httpx rather than adding the
google-genai SDK: the SDK's own request/response shapes would leak through
if we depended on it, fighting the adapter boundary (§M) this whole
platform is built around, and httpx is already a dependency (§4 ladder —
use what's already installed before reaching for something new).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from backend.app.adapters.llm.base import BaseLLMProvider, ProviderMisconfiguredError
from backend.app.adapters.llm.registry import LLMRegistry
from backend.app.core.settings import settings

_BASE_URL = "https://generativelanguage.googleapis.com"


class GeminiProvider(BaseLLMProvider):
    def __init__(self, client: httpx.AsyncClient | None = None):
        if not settings.GEMINI_API_KEY:
            raise ProviderMisconfiguredError(
                "GEMINI_API_KEY is not set — add it to .env before selecting the 'gemini' provider."
            )
        self._model = settings.GEMINI_MODEL
        self._client = client or httpx.AsyncClient(base_url=_BASE_URL, timeout=30.0)

    def _payload(self, prompt: str, system: str, temperature: float) -> dict:
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    @staticmethod
    def _extract_text(candidate_response: dict) -> str:
        try:
            return candidate_response["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return ""

    async def generate_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        response = await self._client.post(
            f"/v1beta/models/{self._model}:generateContent",
            params={"key": settings.GEMINI_API_KEY},
            json=self._payload(prompt, system, temperature),
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    async def stream_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> AsyncIterator[str]:
        async with self._client.stream(
            "POST",
            f"/v1beta/models/{self._model}:streamGenerateContent",
            params={"key": settings.GEMINI_API_KEY, "alt": "sse"},
            json=self._payload(prompt, system, temperature),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                text = self._extract_text(json.loads(line[len("data: "):]))
                if text:
                    yield text

    def get_model_name(self) -> str:
        return self._model


LLMRegistry.register("gemini", GeminiProvider)
