"""
backend/app/adapters/embedding/registry.py

Same self-registering plugin registry pattern as adapters/llm/registry.py
(and ai-sdlc-assistant's LLMFactory before that) — deliberately not shared
code with the LLM registry, since a generic "any-provider registry" would
be exactly the kind of speculative abstraction §30 warns against. Two
concrete ~30-line registries are simpler to read than one generic one with
type parameters bent to fit both shapes.
"""
from __future__ import annotations

import logging

from backend.app.adapters.embedding.base import BaseEmbeddingProvider

logger = logging.getLogger(__name__)


class ProviderAlreadyRegisteredError(Exception):
    pass


class UnknownProviderError(ValueError):
    pass


class EmbeddingRegistry:
    _registry: dict[str, type[BaseEmbeddingProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[BaseEmbeddingProvider]) -> None:
        if name in cls._registry and cls._registry[name] is not provider_class:
            raise ProviderAlreadyRegisteredError(
                f"Embedding provider '{name}' is already registered to "
                f"{cls._registry[name].__name__}; refusing to shadow it with {provider_class.__name__}."
            )
        cls._registry[name] = provider_class
        logger.debug("EmbeddingRegistry: registered '%s' -> %s", name, provider_class.__name__)

    @classmethod
    def create(cls, name: str) -> BaseEmbeddingProvider:
        provider_class = cls._registry.get(name)
        if provider_class is None:
            available = sorted(cls._registry.keys())
            raise UnknownProviderError(f"Unknown embedding provider '{name}'. Registered providers: {available}.")
        return provider_class()

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def reset(cls) -> None:
        cls._registry = {}
