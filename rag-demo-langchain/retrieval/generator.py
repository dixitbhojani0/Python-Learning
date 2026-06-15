import logging
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from core.interfaces import BaseGenerator
from core.models import RetrievedChunk
from core.exceptions import GenerationError
from config.settings import Settings
from retrieval.augmentor import PromptAugmentor

logger = logging.getLogger(__name__)


class GroqGenerator(BaseGenerator):
    """Step 8 — Sends the augmented prompt to Groq LLaMA via an LCEL chain.

    Chain: ChatPromptTemplate | ChatGroq | StrOutputParser
    """

    def __init__(self, settings: Settings, augmentor: PromptAugmentor) -> None:
        llm = ChatGroq(
            model=settings.llm_model,
            api_key=settings.groq_api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        self._chain: Runnable = augmentor.lc_prompt | llm | StrOutputParser()
        self._augmentor = augmentor

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> str:
        try:
            context = PromptAugmentor._format_context(chunks)
            logger.info("Step 8 — Generating answer via Groq LCEL chain")
            return self._chain.invoke({"context": context, "question": question})
        except Exception as exc:
            raise GenerationError("LLM generation failed") from exc
