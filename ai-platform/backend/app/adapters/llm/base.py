"""
backend/app/adapters/llm/base.py

Contract every LLM provider must implement. Services depend on this
interface, never on a vendor SDK directly (§M of the platform blueprint) —
swapping Gemini for Groq for a self-hosted model is a new provider module,
zero changes to any caller.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ProviderMisconfiguredError(Exception):
    """
    Raised by a provider's own __init__ when required config (an API key,
    typically) is missing — distinct from UnknownProviderError (registry.py),
    which means the name itself was never registered. This means the name
    IS valid but the deployment hasn't supplied what it needs to run.
    """


class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        """Return the full generated text for `prompt`."""
        ...

    @abstractmethod
    def stream_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> AsyncIterator[str]:
        """Yield the response incrementally — what the chat streaming endpoint (§Q) consumes."""
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        """Active model identifier, for logging/tracing."""
        ...
