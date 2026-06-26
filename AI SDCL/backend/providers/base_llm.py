"""
backend/providers/base_llm.py

Abstract interface for all LLM providers in this project.

Why ABC (Abstract Base Class)?
  An abstract class defines a CONTRACT — a set of methods that every concrete
  provider MUST implement. If GroqProvider forgets to implement `generate()`,
  Python raises a TypeError at startup, before any request is made.

  This is the Dependency Inversion Principle:
    - High-level components (agents) depend on BaseLLMProvider (the abstraction)
    - Low-level components (GroqProvider) implement that abstraction
    - To swap Groq for Gemini: write GeminiProvider, change 1 line at startup
    - Zero agent code changes required

How agents use this:
  class CrossSourceAgent(BaseAgent):
      def __init__(self, llm: BaseLLMProvider, ...):
          self.llm = llm   # agent never knows if it's Groq or Gemini

  tokens = []
  async for chunk in self.llm.generate(prompt, system, temperature=0.4, max_tokens=1024):
      tokens.append(chunk)
  result = "".join(tokens)
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.providers.llm_response import LLMResponse


class BaseLLMProvider(ABC):
    """
    Interface every LLM provider must implement.

    Methods:
      generate()             — stream response token by token (original, keep for streaming UI)
      generate_text()        — collect full response as LLMResponse (agent reasoning)
      generate_structured()  — collect + parse JSON into LLMResponse.structured
      get_model_name()       — active model name (logging / LangSmith)
      get_model_window()     — context window size (token budget calculation)

    Why keep generate() alongside generate_text()?
      generate() is used for real-time streaming to the user (SSE / Chainlit).
      generate_text() is used for agent internal reasoning (need full text before deciding).
      generate_structured() is generate_text() + JSON parsing inside the provider.
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """
        Stream an LLM response as token chunks.

        Why always stream (not a single return value)?
          - User-facing: Chainlit receives each chunk and displays it immediately
            — user sees text appearing word-by-word, like ChatGPT
          - Agent reasoning: collect all chunks with "".join()
            — same interface, different consumption pattern
          One method handles both. No need for separate complete() and stream() methods.

        Args:
            prompt:      The user's message or agent task description
            system:      System prompt — defines the assistant's role, constraints, output format
            temperature: Controls creativity (0.0 = deterministic, 1.0 = creative/varied)
                         Always read from config/llm.yaml per task, never hardcoded
            max_tokens:  Hard cap on response length — also from config/llm.yaml

        Yields:
            str — one small chunk of tokens at a time (usually a word or few characters)
        """
        ...

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> "LLMResponse":
        """
        Collect the full LLM response as a normalized LLMResponse object.

        Use for agent internal reasoning (CoT, summarization) where streaming
        to the user is not needed — you need the complete text to process it.

        Returns:
            LLMResponse with .text populated, .is_empty=True if LLM returned nothing.
        """
        ...

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> "LLMResponse":
        """
        Collect the full response and extract structured JSON into LLMResponse.structured.

        Provider-specific implementations:
          Groq:   collects token stream → parse_json_block() regex extraction
          Gemini: uses function calling → structured dict directly (no regex needed)
          OpenAI: uses function calling → structured dict directly (no regex needed)

        Agents always receive the same LLMResponse — they never know which path was taken.
        Switch providers → only the provider file changes, not the agents.

        Returns:
            LLMResponse with:
              .text        — full raw output (for debugging)
              .structured  — parsed dict ({} if parse failed)
              .parse_error — True when JSON could not be extracted
              .is_empty    — True when LLM returned nothing
        """
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        """
        Returns the active model name (e.g. 'llama-3.3-70b-versatile').
        Used for logging and LangSmith tracing.
        """
        ...

    @abstractmethod
    def get_model_window(self) -> int:
        """
        Returns the context window size in tokens for the active model.
        Used by ContextBuilder to calculate how many RAG chunks fit in the prompt.
        Example: llama-3.3-70b returns 8192.
        """
        ...

