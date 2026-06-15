import logging
from typing import Optional
from core.interfaces import BaseRetrieverStrategy, BaseAugmentor, BaseGenerator, BaseMemory, BaseReranker
from core.models import ConversationTurn, QueryResult

logger = logging.getLogger(__name__)


class RetrievalPipeline:
    """Orchestrates the 4-step retrieval flow: Retrieve → (Rerank) → Augment → Generate.

    Extensible via constructor injection — swap any component by passing a
    different implementation.  No conditional logic inside the pipeline itself.

    Steps:
      5+6  strategy.retrieve()     — embed query + search (strategy decides how)
      (opt) reranker.rerank()      — re-score with cross-encoder / Cohere API
       7   augmentor.build_prompt() — build RAG prompt
       8   generator.generate()    — call LLM
    """

    def __init__(
        self,
        strategy: BaseRetrieverStrategy,
        augmentor: BaseAugmentor,
        generator: BaseGenerator,
        memory: Optional[BaseMemory] = None,
        reranker: Optional[BaseReranker] = None,
        reranker_top_n: int = 3,
    ) -> None:
        self._strategy = strategy
        self._augmentor = augmentor
        self._generator = generator
        self._memory = memory
        self._reranker = reranker
        self._reranker_top_n = reranker_top_n

    def run(self, question: str, top_k: int) -> QueryResult:
        # ── Steps 5+6: Retrieve ───────────────────────────────────────────────
        logger.info("Steps 5+6 — Retrieving chunks (%s)", type(self._strategy).__name__)
        chunks = self._strategy.retrieve(question, top_k)

        # ── Optional reranking ────────────────────────────────────────────────
        if self._reranker is not None:
            logger.info("Reranking %d chunks → top %d", len(chunks), self._reranker_top_n)
            chunks = self._reranker.rerank(question, chunks, self._reranker_top_n)

        # ── Step 7: Augment ───────────────────────────────────────────────────
        logger.info("Step 7 — Augmenting prompt")
        prompt = self._augmentor.build_prompt(question, chunks)

        # ── Step 8: Generate ──────────────────────────────────────────────────
        logger.info("Step 8 — Generating answer")
        answer = self._generator.generate(question, chunks)

        # ── Memory: persist turn ──────────────────────────────────────────────
        history: list[ConversationTurn] = []
        if self._memory is not None:
            history = self._memory.get_turns() if hasattr(self._memory, "get_turns") else []
            self._memory.add_turn(question, answer)

        # .model_dump() → dict avoids Pydantic class-identity mismatch when
        # Streamlit hot-reloads modules while @st.cache_resource keeps old classes.
        return QueryResult(
            question=question,
            retrieved_chunks=[c.model_dump() for c in chunks],
            answer=answer,
            prompt=prompt,
            query_embedding=self._strategy.last_query_embedding,
            conversation_history=[t.model_dump() for t in history],
        )
