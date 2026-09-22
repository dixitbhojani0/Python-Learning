"""
backend/app/adapters/llm/providers/groq_provider.py

Groq's API is OpenAI-compatible chat completions — called directly via httpx
for the same reason as gemini_provider.py: no vendor SDK dependency, no
vendor response shape leaking past this adapter (§M).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from backend.app.adapters.llm.base import BaseLLMProvider, ProviderMisconfiguredError
from backend.app.adapters.llm.registry import LLMRegistry
from backend.app.core.settings import settings

_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(BaseLLMProvider):
    def __init__(self, client: httpx.AsyncClient | None = None):
        if not settings.GROQ_API_KEY:
            raise ProviderMisconfiguredError(
                "GROQ_API_KEY is not set — add it to .env before selecting the 'groq' provider."
            )
        self._model = settings.GROQ_MODEL
        self._client = client or httpx.AsyncClient(base_url=_BASE_URL, timeout=30.0)

    def _auth_headers(self) -> dict[str, str]:
        # Set per-request, not baked into the client's own default headers —
        # a caller/test injecting its own httpx.AsyncClient (as both happen
        # here) must still get an authenticated request either way.
        return {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}

    def _messages(self, prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def generate_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        response = await self._client.post(
            "/chat/completions",
            headers=self._auth_headers(),
            json={"model": self._model, "messages": self._messages(prompt, system), "temperature": temperature},
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def stream_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> AsyncIterator[str]:
        async with self._client.stream(
            "POST",
            "/chat/completions",
            headers=self._auth_headers(),
            json={
                "model": self._model,
                "messages": self._messages(prompt, system),
                "temperature": temperature,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    break
                delta = json.loads(payload)["choices"][0]["delta"].get("content")
                if delta:
                    yield delta

    def get_model_name(self) -> str:
        return self._model


LLMRegistry.register("groq", GroqProvider)
