import logging
from core.interfaces import BaseVectorStore
from core.models import RetrievedChunk

logger = logging.getLogger(__name__)


class VectorSearcher:
    """Step 6 — Searches ChromaDB by vector similarity to find relevant chunks."""

    def __init__(self, vector_store: BaseVectorStore) -> None:
        self._store = vector_store

    def search(self, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        logger.info("Step 6 — Searching vector DB (top-%d)", top_k)
        return self._store.search(query_embedding, top_k)
