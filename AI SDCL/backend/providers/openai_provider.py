"""
backend/providers/openai_provider.py

OpenAI implementation of BaseLLMProvider.

Uses ChatOpenAI (langchain-openai) for streaming and tool calling.
generate_structured() uses OpenAI's native JSON mode — guaranteed valid JSON,
no regex parsing needed unlike the Groq provider.

Model: gpt-4o (128k context, best tool calling + JSON quality).
No fallback model needed — OpenAI's reliability is far higher than Groq free tier.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from backend.core.config_loader import config
from backend.core.settings import settings
from backend.providers.base_llm import BaseLLMProvider
from backend.providers.llm_response import LLMResponse

logger = logging.getLogger(__name__)

# Re-use the same stream context vars from groq_provider (set by chat.py)
from backend.providers.groq_provider import (
    _active_stream_id,
    _suppress_stream,
    _push_token,
)

_MODEL_WINDOWS: dict[str, int] = {
    "gpt-4o":         128_000,
    "gpt-4o-mini":     128_000,
    "gpt-4-turbo":    128_000,
    "gpt-3.5-turbo":   16_385,
}

_MAX_RETRIES = 3


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "insufficient_quota" in msg or "billing" in msg:
        return False   # quota exhausted — retrying never helps
    return (
        "429"                 in msg or
        "503"                 in msg or
        "rate limit"          in msg or
        "service unavailable" in msg or
        "too many requests"   in msg
    )


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI LLM provider — wraps ChatOpenAI with streaming, retry, and JSON mode.

    Key advantages over GroqProvider for this app:
      - Native JSON mode in generate_structured() → no regex, guaranteed valid JSON
      - 128k context window → full RAG + conversation history fits easily
      - Best-in-class tool calling → MCP gather loop is more reliable
      - No TPD (tokens per day) restrictions on paid tier
    """

    def __init__(self):
        llm_cfg     = config.get_llm_config()
        primary_cfg = llm_cfg.get("primary", {})

        self._model = primary_cfg.get("model", "gpt-4o")
        api_key     = settings.OPENAI_API_KEY

        self._client = ChatOpenAI(
            api_key=api_key,
            model=self._model,
            streaming=True,
        )

        # JSON-mode client for generate_structured() — forces valid JSON output.
        # OpenAI requires at least one mention of "JSON" in the prompt when using this.
        self._json_client = ChatOpenAI(
            api_key=api_key,
            model=self._model,
            streaming=False,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

        # Tool-calling client for the MCP gather loop — deterministic (temp=0).
        self._tool_model = ChatOpenAI(
            api_key=api_key,
            model=self._model,
            temperature=0,
        )

        logger.info("OpenAIProvider: initialised — model='%s'", self._model)

    async def generate(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        messages  = [SystemMessage(content=system), HumanMessage(content=prompt)]
        sid       = _active_stream_id.get("")
        suppress  = _suppress_stream.get(False)
        do_stream = bool(sid) and not suppress

        for attempt in range(_MAX_RETRIES):
            try:
                async for chunk in self._client.astream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if chunk.content:
                        yield chunk.content
                        if do_stream:
                            await _push_token(sid, chunk.content)
                return

            except Exception as exc:
                if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "OpenAIProvider: rate limit (attempt %d/%d) — retrying in %ds | %s",
                        attempt + 1, _MAX_RETRIES, wait, exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("OpenAIProvider: generate failed — %s", exc)
                    return

    def get_chat_model(self):
        """Return ChatOpenAI for the MCP tool-use loop (bind_tools)."""
        return self._tool_model

    def get_model_name(self) -> str:
        return self._model

    def get_model_window(self) -> int:
        return _MODEL_WINDOWS.get(self._model, 128_000)

    async def generate_text(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        from backend.providers.groq_provider import _suppress_stream
        suppress_token = _suppress_stream.set(True)
        try:
            tokens: list[str] = []
            async for chunk in self.generate(prompt, system, temperature, max_tokens):
                tokens.append(chunk)
            text = "".join(tokens)
        finally:
            _suppress_stream.reset(suppress_token)
        return LLMResponse(text=text, model=self._model, is_empty=not text.strip())

    async def generate_structured(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """
        Use OpenAI's native JSON mode — returns guaranteed valid JSON, no regex needed.
        Falls back to text parsing if JSON mode fails for any reason.
        """
        messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
        try:
            resp = await self._json_client.ainvoke(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = resp.content or ""
            if not text.strip():
                return LLMResponse(text="", model=self._model, is_empty=True, parse_error=True)

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # JSON mode should never produce invalid JSON, but handle it defensively
                from backend.agents.base_agent import parse_json_block
                data = parse_json_block(text)

            return LLMResponse(
                text=text,
                model=self._model,
                structured=data,
                parse_error=not bool(data),
            )

        except Exception as exc:
            logger.warning("OpenAIProvider: JSON mode failed (%s) — falling back to text parse", exc)
            return await self._generate_structured_fallback(prompt, system, temperature, max_tokens)

    async def _generate_structured_fallback(self, prompt, system, temperature, max_tokens) -> LLMResponse:
        """Text generation + regex JSON extraction — same as GroqProvider."""
        resp = await self.generate_text(prompt, system, temperature, max_tokens)
        if resp.is_empty:
            resp.parse_error = True
            return resp
        from backend.agents.base_agent import parse_json_block
        data = parse_json_block(resp.text)
        resp.structured  = data
        resp.parse_error = not bool(data)
        return resp


# Self-registration — triggers when __init__.py imports this file
from backend.providers.factory import LLMFactory
LLMFactory.register("openai", OpenAIProvider)
