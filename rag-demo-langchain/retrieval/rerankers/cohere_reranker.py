"""CohereReranker — re-scores retrieved chunks using Cohere's Rerank API.

Activate by setting reranker_enabled=true and reranker_provider=cohere in .env.
Requires: pip install cohere  AND  cohere_api_key set in .env.
"""

import logging
from core.interfaces import BaseReranker
from core.models import RetrievedChunk
from config.settings import Settings

logger = logging.getLogger(__name__)


class CohereReranker(BaseReranker):
    """Wraps Cohere's rerank endpoint as a drop-in post-retrieval step."""

    def __init__(self, settings: Settings) -> None:
        import cohere  # deferred — only needed when reranker is active
        self._client = cohere.Client(api_key=settings.cohere_api_key)
        self._model = "rerank-english-v3.0"

    def rerank(self, question: str, chunks: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
        if not chunks:
            return chunks

        docs = [c.text for c in chunks]
        response = self._client.rerank(
            model=self._model,
            query=question,
            documents=docs,
            top_n=top_n,
        )

        reranked: list[RetrievedChunk] = []
        for rank, result in enumerate(response.results, start=1):
            chunk = chunks[result.index].model_copy(update={
                "score": result.relevance_score,
                "rank": rank,
            })
            reranked.append(chunk)

        logger.info("Reranker: %d → %d chunks", len(chunks), len(reranked))
        return reranked
