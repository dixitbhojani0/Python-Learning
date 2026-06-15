import logging
from core.interfaces import BaseEmbedder, BaseRetrieverStrategy, BaseVectorStore
from core.models import RetrievedChunk
from config.settings import Settings

logger = logging.getLogger(__name__)


class BasicRetrieverStrategy(BaseRetrieverStrategy):
    """Steps 5+6 — embed the question once, then search by cosine similarity.

    This is the default strategy (retriever_strategy=basic in .env).
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
        settings: Settings,
    ) -> None:
        self._embedder = embedder
        self._store = vector_store
        self._last_embedding: list[float] = []

    def retrieve(self, question: str, top_k: int) -> list[RetrievedChunk]:
        logger.info("Step 5 — BasicStrategy: embedding query")
        self._last_embedding = self._embedder.embed_query(question)
        logger.info("Step 6 — BasicStrategy: searching vector DB (top-%d)", top_k)
        return self._store.search(self._last_embedding, top_k)

    @property
    def last_query_embedding(self) -> list[float]:
        return self._last_embedding
