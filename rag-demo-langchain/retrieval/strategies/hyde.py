"""HyDEStrategy — Hypothetical Document Embedding.

Asks the LLM to write a *hypothetical answer* to the question, embeds that
answer, then searches with it instead of the raw query.

This closes the gap between short questions and longer doc chunks.
Activate by setting retriever_strategy=hyde in .env.

Reference: Gao et al. 2022  (https://arxiv.org/abs/2212.10496)
"""

import logging
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from core.interfaces import BaseEmbedder, BaseRetrieverStrategy, BaseVectorStore
from core.models import RetrievedChunk
from config.settings import Settings

logger = logging.getLogger(__name__)

_HYDE_PROMPT = ChatPromptTemplate.from_template(
    "Write a short, factual paragraph (3-5 sentences) that directly answers "
    "the following question. Do not hedge — write as if you are certain:\n\n"
    "{question}"
)


class HyDEStrategy(BaseRetrieverStrategy):
    """Steps 5+6 — generate a hypothetical answer, embed it, then search."""

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
        settings: Settings,
    ) -> None:
        self._embedder = embedder
        self._store = vector_store
        self._last_embedding: list[float] = []

        llm = ChatGroq(
            model=settings.llm_model,
            api_key=settings.groq_api_key,
            temperature=0.2,
            max_tokens=300,
        )
        self._chain = _HYDE_PROMPT | llm | StrOutputParser()

    def retrieve(self, question: str, top_k: int) -> list[RetrievedChunk]:
        logger.info("Step 5 — HyDEStrategy: generating hypothetical document")
        hypothetical_doc = self._chain.invoke({"question": question})
        logger.debug("HyDE doc: %s", hypothetical_doc[:120])

        self._last_embedding = self._embedder.embed_query(hypothetical_doc)
        logger.info("Step 6 — HyDEStrategy: searching with hypothetical embedding (top-%d)", top_k)
        return self._store.search(self._last_embedding, top_k)

    @property
    def last_query_embedding(self) -> list[float]:
        return self._last_embedding
