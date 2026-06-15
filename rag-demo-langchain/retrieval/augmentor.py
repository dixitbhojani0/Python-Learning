import logging
from langchain_core.prompts import ChatPromptTemplate
from core.interfaces import BaseAugmentor
from core.models import RetrievedChunk

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a helpful assistant. Answer the question using ONLY the provided context. "
    "When the question asks for a list (e.g. 'what are all the X', 'list every Y'), "
    "enumerate EVERY item found across ALL context chunks — do not skip, summarize, or say 'and more'. "
    "If the answer is not in the context, say \"I don't have enough information to answer this.\""
)

_HUMAN = "Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"


class PromptAugmentor(BaseAugmentor):
    """Step 7 — Combines the question + retrieved chunks into an augmented prompt.

    Exposes both a plain string (for UI display) and a LangChain
    ChatPromptTemplate (used by the generator's LCEL chain).
    """

    def __init__(self) -> None:
        self.lc_prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM),
            ("human", _HUMAN),
        ])

    def build_prompt(self, question: str, chunks: list[RetrievedChunk]) -> str:
        context = self._format_context(chunks)
        logger.info("Step 7 — Building augmented prompt (%d chunks)", len(chunks))
        return f"[System]\n{_SYSTEM}\n\n[Context]\n{context}\n\n[Question]\n{question}"

    @staticmethod
    def _format_context(chunks: list[RetrievedChunk]) -> str:
        return "\n\n---\n\n".join(
            f"[Source: {c.source} | chunk #{c.chunk_index}]\n{c.text}"
            for c in chunks
        )
