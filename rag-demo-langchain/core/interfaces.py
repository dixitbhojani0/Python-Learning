from abc import ABC, abstractmethod
from typing import Optional
from core.models import Chunk, RawDocument, DocStats, RetrievedChunk


class BaseLoader(ABC):
    @abstractmethod
    def load(self, docs_dir: str) -> tuple[list[RawDocument], list[DocStats]]:
        """Step 1 — Load raw text from files."""


class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, docs: list[RawDocument]) -> list[Chunk]:
        """Step 2 — Split raw documents into overlapping chunks."""


class BaseEmbedder(ABC):
    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Step 3 — Convert a batch of texts to embedding vectors."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Step 5 — Convert a single query to an embedding vector."""


class BaseVectorStore(ABC):
    @abstractmethod
    def store(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Step 4 — Persist chunks + vectors to the vector database."""

    @abstractmethod
    def search(self, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        """Step 6 — Find top-K semantically similar chunks by vector."""

    @abstractmethod
    def is_indexed(self) -> bool: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def get_all(self) -> dict[str, list[dict]]: ...


class BaseAugmentor(ABC):
    @abstractmethod
    def build_prompt(self, question: str, chunks: list[RetrievedChunk]) -> str:
        """Step 7 — Combine question + retrieved chunks into an LLM prompt."""


class BaseGenerator(ABC):
    @abstractmethod
    def generate(self, question: str, chunks: list[RetrievedChunk]) -> str:
        """Step 8 — Call the LLM and return the grounded answer."""


# ── Extension points (swap via config, no code changes) ─────────────────────

class BaseRetrieverStrategy(ABC):
    """Steps 5+6 combined — embed query, optionally expand it, then search.

    Concrete strategies: BasicRetrieverStrategy, MultiQueryStrategy, HyDEStrategy.
    Swap by setting retriever_strategy in .env / Settings.
    """

    @abstractmethod
    def retrieve(self, question: str, top_k: int) -> list[RetrievedChunk]:
        """Return top-K relevant chunks for the given question."""

    @property
    @abstractmethod
    def last_query_embedding(self) -> list[float]:
        """The embedding used for the most recent retrieve() call (for UI display)."""


class BaseMemory(ABC):
    """Optional conversation memory — inject via ComponentFactory when memory_enabled=True."""

    @abstractmethod
    def add_turn(self, question: str, answer: str) -> None:
        """Persist a completed Q&A turn."""

    @abstractmethod
    def get_history(self) -> list[dict]:
        """Return recent turns as [{"role": "user"|"assistant", "content": str}, ...]."""

    @abstractmethod
    def clear(self) -> None:
        """Wipe all stored turns."""


class BaseReranker(ABC):
    """Optional reranker — inject via ComponentFactory when reranker_enabled=True.

    Takes the initial vector-search results and re-scores them with a
    cross-encoder or an API (e.g., Cohere Rerank).
    """

    @abstractmethod
    def rerank(self, question: str, chunks: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
        """Return top_n chunks re-ranked by relevance to question."""
