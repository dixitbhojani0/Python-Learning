"""
backend/app/tools/registry.py

Same self-registering plugin registry pattern as adapters/llm/registry.py
and adapters/embedding/registry.py (and ai-sdlc-assistant's LLMFactory
before all three) — a third independent ~40-line copy rather than a shared
generic registry, for the same reason the embedding registry gave: one
generic registry bent to fit three shapes is harder to read than three
concrete ones (§30).
"""
from __future__ import annotations

import logging

from backend.app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ProviderAlreadyRegisteredError(Exception):
    pass


class UnknownProviderError(ValueError):
    pass


class ToolRegistry:
    _registry: dict[str, type[BaseTool]] = {}

    @classmethod
    def register(cls, name: str, tool_class: type[BaseTool]) -> None:
        if name in cls._registry and cls._registry[name] is not tool_class:
            raise ProviderAlreadyRegisteredError(
                f"Tool '{name}' is already registered to {cls._registry[name].__name__}; "
                f"refusing to shadow it with {tool_class.__name__}."
            )
        cls._registry[name] = tool_class
        logger.debug("ToolRegistry: registered '%s' -> %s", name, tool_class.__name__)

    @classmethod
    def create(cls, name: str) -> BaseTool:
        tool_class = cls._registry.get(name)
        if tool_class is None:
            available = sorted(cls._registry.keys())
            raise UnknownProviderError(f"Unknown tool '{name}'. Registered tools: {available}.")
        return tool_class()

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def reset(cls) -> None:
        cls._registry = {}
