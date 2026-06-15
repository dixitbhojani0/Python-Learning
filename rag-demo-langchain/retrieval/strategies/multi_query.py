"""MultiQueryStrategy — generates N paraphrases of the user's question,
embeds each, retrieves results, then deduplicates by chunk ID.

Reduces retrieval gaps caused by vocabulary mismatch.
Activate by setting retriever_strategy=multi_query in .env.
"""

import logging
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from core.interfaces import BaseEmbedder, BaseRetrieverStrategy, BaseVectorStore
from core.models import RetrievedChunk
from config.settings import Settings

logger = logging.getLogger(__name__)

_PARAPHRASE_PROMPT = ChatPromptTemplate.from_template(
    "Generate {n} different phrasings of the following question, "
    "one per line, no numbering:\n\n{question}"
)


class MultiQueryStrategy(BaseRetrieverStrategy):
    """Steps 5+6 — multi-query expansion before vector search."""

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
        settings: Settings,
        n_queries: int = 3,
    ) -> None:
        self._embedder = embedder
        self._store = vector_store
        self._n = n_queries
        self._last_embedding: list[float] = []

        llm = ChatGroq(
            model=settings.llm_model,
            api_key=settings.groq_api_key,
            temperature=0.4,
            max_tokens=200,
        )
        self._chain = _PARAPHRASE_PROMPT | llm | StrOutputParser()

    def retrieve(self, question: str, top_k: int) -> list[RetrievedChunk]:
        logger.info("Step 5 — MultiQueryStrategy: generating %d paraphrases", self._n)
        raw = self._chain.invoke({"question": question, "n": self._n})
        variants = [q.strip() for q in raw.strip().splitlines() if q.strip()]
        all_queries = [question] + variants[:self._n]

        # Embed original question for UI display
        self._last_embedding = self._embedder.embed_query(question)

        seen_ids: set[str] = set()
        merged: list[RetrievedChunk] = []

        for q in all_queries:
            vec = self._embedder.embed_query(q)
            for chunk in self._store.search(vec, top_k):
                if chunk.id not in seen_ids:
                    seen_ids.add(chunk.id)
                    merged.append(chunk)

        # Re-rank by score descending, trim to top_k, fix rank numbers
        merged.sort(key=lambda c: c.score, reverse=True)
        final = merged[:top_k]
        for i, c in enumerate(final, start=1):
            c.rank = i
        logger.info("Step 6 — MultiQueryStrategy: %d unique chunks from %d queries", len(final), len(all_queries))
        return final

    @property
    def last_query_embedding(self) -> list[float]:
        return self._last_embedding
