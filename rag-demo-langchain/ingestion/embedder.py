import logging
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from core.interfaces import BaseEmbedder
from core.exceptions import EmbedError
from config.settings import Settings

logger = logging.getLogger(__name__)


class FastEmbedder(BaseEmbedder):
    """Step 3 / Step 5 — Converts text to vectors using FastEmbed (local, no API key).

    Single instance is shared between ingestion (embed_documents) and
    retrieval (embed_query) so the model loads into memory only once.
    """

    def __init__(self, settings: Settings) -> None:
        logger.info("Loading embedding model: %s", settings.embedding_model)
        self._model = FastEmbedEmbeddings(model_name=settings.embedding_model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            logger.info("Embedding %d chunks", len(texts))
            return self._model.embed_documents(texts)
        except Exception as exc:
            raise EmbedError("Failed to embed documents") from exc

    def embed_query(self, text: str) -> list[float]:
        try:
            logger.info("Embedding query: %.60s...", text)
            return self._model.embed_query(text)
        except Exception as exc:
            raise EmbedError("Failed to embed query") from exc
