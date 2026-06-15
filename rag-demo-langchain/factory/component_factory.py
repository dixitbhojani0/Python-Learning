"""Registry-based factory — swap LLM, embedder, strategy, memory, or reranker
by changing a single setting in .env.  No if/elif chains, no code changes.

Adding a new provider:
  1. Implement the relevant Base* interface.
  2. Register the class in the matching _REGISTRY dict below.
  3. Set the corresponding *_provider key in .env.
"""

import logging
from typing import Optional, Type

from config.settings import Settings
from core.interfaces import (
    BaseEmbedder,
    BaseGenerator,
    BaseMemory,
    BaseReranker,
    BaseRetrieverStrategy,
    BaseVectorStore,
)

logger = logging.getLogger(__name__)

# ── Registries ────────────────────────────────────────────────────────────────
# Populated by _register_defaults() at module import time.

_EMBEDDER_REGISTRY:  dict[str, Type[BaseEmbedder]]          = {}
_GENERATOR_REGISTRY: dict[str, Type[BaseGenerator]]         = {}
_STRATEGY_REGISTRY:  dict[str, Type[BaseRetrieverStrategy]] = {}
_MEMORY_REGISTRY:    dict[str, Type[BaseMemory]]            = {}
_RERANKER_REGISTRY:  dict[str, Type[BaseReranker]]          = {}


def _register_defaults() -> None:
    # Embedders
    from ingestion.embedder import FastEmbedder
    _EMBEDDER_REGISTRY["fastembed"] = FastEmbedder

    # Generators
    from retrieval.generator import GroqGenerator
    _GENERATOR_REGISTRY["groq"] = GroqGenerator

    # Retrieval strategies
    from retrieval.strategies.basic import BasicRetrieverStrategy
    from retrieval.strategies.multi_query import MultiQueryStrategy
    from retrieval.strategies.hyde import HyDEStrategy
    _STRATEGY_REGISTRY["basic"]       = BasicRetrieverStrategy
    _STRATEGY_REGISTRY["multi_query"] = MultiQueryStrategy
    _STRATEGY_REGISTRY["hyde"]        = HyDEStrategy

    # Memory backends
    from retrieval.memory.in_memory import InMemoryMemory
    _MEMORY_REGISTRY["in_memory"] = InMemoryMemory

    # Rerankers (optional — only imported when needed to avoid hard dependency)
    try:
        from retrieval.rerankers.cohere_reranker import CohereReranker
        _RERANKER_REGISTRY["cohere"] = CohereReranker
    except ImportError:
        pass  # cohere package not installed; reranker stays unavailable


_register_defaults()


# ── Factory ───────────────────────────────────────────────────────────────────

class ComponentFactory:
    """Reads settings and returns fully-wired component instances.

    The factory owns the shared embedder + vector_store so they are
    instantiated exactly once and reused across ingestion and retrieval.
    """

    def __init__(self, settings: Settings, vector_store: BaseVectorStore) -> None:
        self._settings = settings
        self._vector_store = vector_store
        # Shared embedder — loaded into memory once, used by both pipelines
        self._embedder: BaseEmbedder = self._build_embedder()

    # ── Public accessors ──────────────────────────────────────────────────────

    @property
    def embedder(self) -> BaseEmbedder:
        return self._embedder

    def build_generator(self) -> BaseGenerator:
        from retrieval.augmentor import PromptAugmentor
        augmentor = PromptAugmentor()
        key = self._settings.llm_provider
        cls = self._lookup(_GENERATOR_REGISTRY, key, "llm_provider")
        logger.info("Building generator: %s", cls.__name__)
        return cls(self._settings, augmentor)

    def build_retriever_strategy(self) -> BaseRetrieverStrategy:
        key = self._settings.retriever_strategy
        cls = self._lookup(_STRATEGY_REGISTRY, key, "retriever_strategy")
        logger.info("Building retriever strategy: %s", cls.__name__)
        return cls(self._embedder, self._vector_store, self._settings)

    def build_memory(self) -> Optional[BaseMemory]:
        if not self._settings.memory_enabled:
            return None
        cls = self._lookup(_MEMORY_REGISTRY, "in_memory", "memory")
        logger.info("Building memory: %s", cls.__name__)
        return cls(max_turns=self._settings.memory_max_turns)

    def build_reranker(self) -> Optional[BaseReranker]:
        if not self._settings.reranker_enabled:
            return None
        key = self._settings.reranker_provider
        cls = self._lookup(_RERANKER_REGISTRY, key, "reranker_provider")
        logger.info("Building reranker: %s", cls.__name__)
        return cls(self._settings)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_embedder(self) -> BaseEmbedder:
        key = self._settings.embedding_provider
        cls = self._lookup(_EMBEDDER_REGISTRY, key, "embedding_provider")
        logger.info("Building embedder: %s", cls.__name__)
        return cls(self._settings)

    @staticmethod
    def _lookup(registry: dict, key: str, setting_name: str):
        if key not in registry:
            available = ", ".join(registry.keys()) or "(none registered)"
            raise ValueError(
                f"Unknown {setting_name}='{key}'. "
                f"Available: {available}"
            )
        return registry[key]
