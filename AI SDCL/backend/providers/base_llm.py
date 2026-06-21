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
from typing import AsyncGenerator


class BaseLLMProvider(ABC):
    """
    Interface every LLM provider must implement.

    Two methods are required:
      generate()         — stream a response token by token
      get_model_name()   — return which model is active (for logging)
      get_model_window() — return context window size (for token budget calculation)
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

        --- Example 1: Collect full response (agent internal reasoning) ---
            tokens = []
            async for chunk in provider.generate(prompt, system, temperature=0.1, max_tokens=500):
                tokens.append(chunk)
            result = "".join(tokens)

        --- Example 2: Stream to Chainlit (user-facing response) ---
            async for chunk in provider.generate(prompt, system, temperature=0.4, max_tokens=1024):
                await chainlit_message.stream_token(chunk)
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
