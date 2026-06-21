"""
backend/providers/groq_provider.py

Groq implementation of BaseLLMProvider.

Uses ChatGroq (from langchain-groq) to make the actual API calls.
All configuration — model name, temperatures, token limits — is read from
config/llm.yaml. Nothing is hardcoded here.

Resilience strategy (as per resilience_standards.md):
  PRIMARY model: llama-3.3-70b-versatile
    Attempt 1 → wait 2s on rate limit → Attempt 2 → wait 4s → Attempt 3 → give up

  FALLBACK model: llama-3.1-8b-instant
    Triggered only if primary fails all 3 attempts.
    Smaller and faster, slightly lower quality — but better than no answer.

  COMPLETE failure (both models fail):
    Log the error, yield nothing. The caller (agent or route) receives an empty
    string and is responsible for returning a graceful degradation response.

Why retry on 429/503 only?
  429 = rate limit (Groq free tier) — temporary, resolves in a few seconds
  503 = service unavailable — temporary outage
  400/401/422 = our bug (bad request, wrong API key, invalid format) — retrying won't help

Why NOT use tenacity @retry decorator here?
  Tenacity's @retry decorator works on regular async functions (that return a value).
  Our generate() uses `yield` — making it an async generator. Python does not allow
  @retry on async generators because generators are lazily consumed, not called once.
  Solution: implement retry manually inside the generator with a for loop.
  Tenacity will be used in Phase 6 for MCP connector calls (regular async functions).
"""
import asyncio
import logging
from typing import AsyncGenerator

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from backend.core.config_loader import config
from backend.core.settings import settings
from backend.providers.base_llm import BaseLLMProvider

logger = logging.getLogger(__name__)

# Context window sizes per Groq-hosted model (in tokens)
# Used by get_model_window() so ContextBuilder knows the budget
_MODEL_WINDOWS: dict[str, int] = {
    "llama-3.3-70b-versatile": 8192,
    "llama-3.1-8b-instant":    8192,
    "mixtral-8x7b-32768":      32768,
}

# Maximum attempts for primary model before trying fallback
_MAX_RETRIES = 3


def _is_retryable(exc: Exception) -> bool:
    """
    Inspect an exception and decide if it's worth retrying.

    We check the error message string because LangChain wraps Groq errors
    into generic exceptions — there's no clean GroqRateLimitError class to catch.
    Checking for "429" or "503" in the message is the reliable approach.
    """
    msg = str(exc).lower()
    return (
        "429"                 in msg or
        "503"                 in msg or
        "rate limit"          in msg or
        "service unavailable" in msg or
        "too many requests"   in msg
    )


class GroqProvider(BaseLLMProvider):
    """
    Groq LLM provider — wraps ChatGroq with streaming, retry, and automatic fallback.

    Created once at application startup and injected into agents via the
    BaseLLMProvider interface. Agents never know this is Groq specifically.

    --- Collecting a full response (agent reasoning) ---
        provider = GroqProvider()
        tokens = []
        async for chunk in provider.generate(
            prompt="Summarise sprint 12 risks",
            system="You are an SDLC analyst. Be concise.",
            temperature=0.1,
            max_tokens=500,
        ):
            tokens.append(chunk)
        result = "".join(tokens)   # → "Sprint 12 has 3 blocked tickets..."

    --- Streaming to a user (Chainlit UI) ---
        msg = cl.Message(content="")
        async for chunk in provider.generate(prompt, system, 0.4, 1024):
            await msg.stream_token(chunk)
        await msg.send()
    """

    def __init__(self):
        llm_cfg      = config.get_llm_config()
        primary_cfg  = llm_cfg.get("primary",  {})
        fallback_cfg = llm_cfg.get("fallback", {})

        self._primary_model  = primary_cfg.get("model",  settings.GROQ_MODEL)
        self._fallback_model = fallback_cfg.get("model", "llama-3.1-8b-instant")

        # Build both ChatGroq clients once at startup.
        # Creating ChatGroq is cheap — it's just configuring an HTTP client.
        # The actual API call only happens when generate() is invoked.
        self._primary_client = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model=self._primary_model,
            streaming=True,
        )
        self._fallback_client = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model=self._fallback_model,
            streaming=True,
        )

        logger.info(
            "GroqProvider: initialised — primary='%s'  fallback='%s'",
            self._primary_model,
            self._fallback_model,
        )

    async def generate(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response tokens from Groq.

        Tries primary model first (up to 3 attempts with backoff).
        Falls back to smaller model if primary exhausts all retries.
        Returns nothing on complete failure — caller handles graceful degradation.
        """
        messages = [
            SystemMessage(content=system),   # defines who the LLM is
            HumanMessage(content=prompt),    # the actual task or question
        ]

        # ── Try primary model with retry on transient errors ──────────────────
        for attempt in range(_MAX_RETRIES):
            try:
                async for chunk in self._primary_client.astream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    # chunk.content can be empty string on the last chunk (stop signal)
                    # We only yield when there's actual content
                    if chunk.content:
                        yield chunk.content

                return  # ← Successful stream completed, exit generate()

            except Exception as exc:
                if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                    # Rate limit or temporary outage — worth waiting and retrying
                    wait_seconds = 2 ** (attempt + 1)   # attempt 0→2s, attempt 1→4s
                    logger.warning(
                        "GroqProvider: primary hit rate limit (attempt %d/%d) — retrying in %ds | error: %s",
                        attempt + 1, _MAX_RETRIES, wait_seconds, exc,
                    )
                    await asyncio.sleep(wait_seconds)
                    # Loop continues to next attempt

                else:
                    # Either: not a retryable error, or: we've used all retries
                    if attempt == _MAX_RETRIES - 1:
                        logger.error(
                            "GroqProvider: primary model '%s' failed all %d attempts — trying fallback",
                            self._primary_model, _MAX_RETRIES,
                        )
                    else:
                        logger.error(
                            "GroqProvider: primary model failed with non-retryable error — trying fallback | %s",
                            exc,
                        )
                    break  # Exit retry loop, fall through to fallback

        # ── Fallback: smaller model, one attempt ──────────────────────────────
        # If we're here, primary failed. Try llama-3.1-8b-instant (faster, smaller).
        try:
            logger.info("GroqProvider: using fallback model '%s'", self._fallback_model)
            async for chunk in self._fallback_client.astream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                if chunk.content:
                    yield chunk.content

        except Exception:
            # Both models failed. Log it and return nothing.
            # The caller (agent or API route) receives an empty string.
            # It's the caller's responsibility to return a graceful degradation message.
            logger.exception(
                "GroqProvider: fallback model '%s' also failed — returning empty response",
                self._fallback_model,
            )
            return

    def get_model_name(self) -> str:
        """Returns the primary model name — used for logging and LangSmith traces."""
        return self._primary_model

    def get_model_window(self) -> int:
        """
        Returns context window size for the primary model.
        ContextBuilder uses this to calculate how many tokens are available for RAG chunks.
        Default to 8192 if model is unknown.
        """
        return _MODEL_WINDOWS.get(self._primary_model, 8192)
