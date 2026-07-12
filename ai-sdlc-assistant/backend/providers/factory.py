"""
backend/providers/factory.py

Plugin Registry for LLM providers.

Design pattern: Plugin Registry (also called Service Registry / Provider Registry).

How it works:
  1. Each provider file calls LLMFactory.register() at its own bottom.
  2. backend/providers/__init__.py imports all provider files, triggering their register() calls.
  3. This file (_factory.py_) does a simple dict lookup — no if/elif, no knowledge of specific providers.
  4. To add a new LLM: create the provider file, add register() at its bottom, add 1 import to __init__.py.
     THIS FILE NEVER NEEDS TO BE EDITED.

Why Registry instead of if/elif Factory?
  if/elif violates the Open/Closed Principle:
    "Software should be open for extension, closed for modification."
  Every new provider in an if/elif factory requires editing the factory itself —
  risking breakage of existing providers and coupling the factory to concrete classes.

  The Registry inverts this: providers register themselves. The factory is unaware
  of any specific provider class. It's a lookup table, nothing more.

Comparison:
  Django uses this for database backends: "django.db.backends.postgresql"
  SQLAlchemy uses this for dialects: "postgresql+psycopg2"
  FastAPI uses this for exception handlers.

Usage (from graph.py, metrics.py, scheduler, etc.):
    from backend.providers.factory import LLMFactory
    provider = LLMFactory.get_provider()   # returns BaseLLMProvider singleton
"""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.providers.base_llm import BaseLLMProvider

logger = logging.getLogger(__name__)


class LLMFactory:
    """
    Plugin Registry — maps provider names to provider classes.

    Thread-safe for read-heavy workloads (singleton is set once at startup,
    never mutated after that).
    """

    # Registry: { "groq": GroqProvider, "gemini": GeminiProvider, ... }
    _registry: dict[str, type] = {}

    # Singleton instance — created once on first get_provider() call
    _instance: "BaseLLMProvider | None" = None

    # ── Registration ──────────────────────────────────────────────────────────

    @classmethod
    def register(cls, name: str, provider_class: type) -> None:
        """
        Register a provider class under a name.

        Called at the bottom of each provider file when that file is imported.
        Example (groq_provider.py, last line):
            LLMFactory.register("groq", GroqProvider)

        Args:
            name:           Short identifier matching config/llm.yaml provider key (e.g. "groq")
            provider_class: The class (not instance) — LLMFactory creates the instance
        """
        cls._registry[name] = provider_class
        logger.debug(
            "LLMFactory: registered provider '%s' → %s",
            name, provider_class.__name__,
        )

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def get_provider(cls) -> "BaseLLMProvider":
        """
        Return the singleton LLM provider configured in llm.yaml.

        Creates the provider on first call, then caches it for the application lifetime.
        Reads `provider` key from llm.yaml — defaults to "groq" if not set.

        Raises:
            ValueError if the configured provider name is not registered.
            ImportError if the provider's dependencies are not installed.
        """
        if cls._instance is None:
            from backend.core.config_loader import config
            name = config.get_llm_config().get("provider", "groq")
            cls._instance = cls._create(name)
        return cls._instance

    @classmethod
    def _create(cls, name: str) -> "BaseLLMProvider":
        """
        Instantiate the provider by looking up its class in the registry.

        No if/elif here — pure dict lookup. This method never needs to change
        when new providers are added. Registration happens in provider files.
        """
        provider_class = cls._registry.get(name)
        if provider_class is None:
            available = sorted(cls._registry.keys())
            raise ValueError(
                f"Unknown LLM provider: {name!r}. "
                f"Registered providers: {available}. "
                f"To add a new provider:\n"
                f"  1. Create backend/providers/{name}_provider.py\n"
                f"  2. Call LLMFactory.register('{name}', YourProvider) at the bottom\n"
                f"  3. Import it in backend/providers/__init__.py\n"
                f"  4. Set 'provider: {name}' in config/llm.yaml"
            )
        logger.info(
            "LLMFactory: creating '%s' provider (class=%s)",
            name, provider_class.__name__,
        )
        return provider_class()

    # ── Test utilities ────────────────────────────────────────────────────────

    @classmethod
    def reset(cls) -> None:
        """
        Force re-creation of the singleton on next get_provider() call.
        Only use in tests — never in production code.
        """
        cls._instance = None
        logger.debug("LLMFactory: singleton reset (test mode)")
