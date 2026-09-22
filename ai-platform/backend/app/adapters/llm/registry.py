"""
backend/app/adapters/llm/registry.py

Self-registering plugin registry for LLM providers — the exact pattern
already proven in this org's ai-sdlc-assistant project
(backend/providers/factory.py's LLMFactory), generalized here.

How to add a new provider (no edits to this file, ever):
  1. Create backend/app/adapters/llm/providers/<name>_provider.py
  2. Implement BaseLLMProvider
  3. Call LLMRegistry.register("<name>", YourProviderClass) at the bottom of that file
  4. Import that module in backend/app/adapters/llm/providers/__init__.py

This file only ever does a dict lookup — it has no knowledge of any concrete
provider class, so it never needs to change when a new one is added
(Open/Closed Principle in its strictest form).
"""
from __future__ import annotations

import logging

from backend.app.adapters.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class ProviderAlreadyRegisteredError(Exception):
    """
    Raised when a second provider tries to register under a name already taken.

    Deliberately an error, not a silent overwrite: a duplicate name is either a
    copy-paste bug or two providers unintentionally shadowing each other — both
    are bugs worth surfacing immediately, not masking behind "last import wins."
    """


class UnknownProviderError(ValueError):
    """Raised when a config asks for a provider name that was never registered."""


class LLMRegistry:
    _registry: dict[str, type[BaseLLMProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[BaseLLMProvider]) -> None:
        if name in cls._registry and cls._registry[name] is not provider_class:
            raise ProviderAlreadyRegisteredError(
                f"LLM provider '{name}' is already registered to "
                f"{cls._registry[name].__name__}; refusing to shadow it with "
                f"{provider_class.__name__}."
            )
        cls._registry[name] = provider_class
        logger.debug("LLMRegistry: registered '%s' -> %s", name, provider_class.__name__)

    @classmethod
    def create(cls, name: str) -> BaseLLMProvider:
        """Instantiate the named provider. Raises UnknownProviderError if never registered."""
        provider_class = cls._registry.get(name)
        if provider_class is None:
            available = sorted(cls._registry.keys())
            raise UnknownProviderError(
                f"Unknown LLM provider '{name}'. Registered providers: {available}."
            )
        return provider_class()

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def reset(cls) -> None:
        """Test-only: clear the registry between tests so registration tests are isolated."""
        cls._registry = {}
