import logging
from core.interfaces import BaseEmbedder
from core.exceptions import EmbedError

logger = logging.getLogger(__name__)


class QueryEmbedder:
    """Step 5 — Embeds the user's question into a vector.

    Reuses the same BaseEmbedder instance from ingestion so the model
    is loaded into memory only once across both pipelines.
    """

    def __init__(self, embedder: BaseEmbedder) -> None:
        self._embedder = embedder

    def embed(self, question: str) -> list[float]:
        logger.info("Step 5 — Embedding query")
        return self._embedder.embed_query(question)
