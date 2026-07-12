"""
backend/providers/gemini_provider.py

Gemini implementation of BaseLLMProvider.

Uses ChatGoogleGenerativeAI (langchain-google-genai) for streaming, JSON mode,
and tool calling. Free tier via https://aistudio.google.com — no billing needed,
1M token context, native structured output (no regex parsing unlike Groq).

No fallback model needed — same reasoning as OpenAIProvider: Gemini's free tier
is generous enough on its own, and a same-model retry (below) already covers
transient rate limits.

Safety settings are explicitly relaxed (BLOCK_NONE for every category). Gemini's
default filters can silently return an EMPTY response (not an exception) for
ordinary SDLC text containing words like "crash", "error", "broken", "failing" —
exactly what shows up in Jira/Slack content. Without this override, _guard_empty_llm
in base_agent.py would trip its "temporarily unavailable" fallback for the wrong
reason (safety block, not quota/rate-limit).
"""
import asyncio
import logging
from typing import AsyncGenerator

from langchain_google_genai import ChatGoogleGenerativeAI, HarmBlockThreshold, HarmCategory
from langchain_core.messages import HumanMessage, SystemMessage

from backend.core.config_loader import config
from backend.core.settings import settings
from backend.providers.base_llm import BaseLLMProvider
from backend.providers.llm_response import LLMResponse

# Re-use the same stream context vars the other providers push tokens through
from backend.providers.groq_provider import (
    _active_stream_id,
    _suppress_stream,
    _push_token,
)

logger = logging.getLogger(__name__)

# gemini-2.0-flash / 2.0-flash-lite are SHUT DOWN — not listed on purpose.
_MODEL_WINDOWS: dict[str, int] = {
    "gemini-2.5-flash":      1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
    "gemini-2.5-pro":        1_048_576,
    "gemini-3.1-flash-lite": 1_048_576,
    "gemini-3.5-flash":      1_048_576,
}

_MAX_RETRIES = 3

# No timeout hangs forever (see .claude/standards/resilience_standards.md,
# Pattern 4) — confirmed live: a stuck call to gemini-3.5-flash sat with zero
# activity for minutes until this was added. 30s matches this app's other
# LLM calls (connect~5s + read~25s budget).
_REQUEST_TIMEOUT = 30.0

# Relaxed for every category — SDLC content (bug reports, incident postmortems,
# "crash"/"failing"/"broken") routinely trips default safety thresholds.
_SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT:        HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH:       HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}


def _is_retryable(exc: Exception) -> bool:
    """Same string-matching approach as groq_provider/openai_provider — the
    langchain wrapper doesn't expose a clean exception hierarchy either."""
    msg = str(exc).lower()
    if "quota" in msg or "billing" in msg:
        return False  # quota exhausted — retrying never helps
    return (
        "429"                 in msg or
        "503"                 in msg or
        "resourceexhausted"   in msg or
        "rate limit"          in msg or
        "service unavailable" in msg or
        "too many requests"   in msg or
        "timeout"             in msg or
        "deadline"            in msg
    )


class GeminiProvider(BaseLLMProvider):
    """
    Gemini LLM provider — wraps ChatGoogleGenerativeAI with streaming, retry,
    native JSON mode, and tool calling.
    """

    def __init__(self):
        llm_cfg     = config.get_llm_config()
        primary_cfg = llm_cfg.get("primary", {})

        self._model   = primary_cfg.get("model", settings.GEMINI_MODEL)
        self._api_key = settings.GEMINI_API_KEY

        # Tool-calling client for the MCP gather loop — deterministic (temp=0),
        # built once since its temperature/max_tokens never change per call.
        self._tool_model = ChatGoogleGenerativeAI(
            google_api_key=self._api_key,
            model=self._model,
            temperature=0,
            safety_settings=_SAFETY_SETTINGS,
            timeout=_REQUEST_TIMEOUT,
        )

        logger.info("GeminiProvider: initialised — model='%s'", self._model)

    def _make_client(self, temperature: float, max_tokens: int, json_mode: bool = False):
        """
        Build a client for this call's temperature/max_tokens.

        Unlike ChatGroq/ChatOpenAI, this SDK version's ChatGoogleGenerativeAI does
        NOT accept temperature/max_output_tokens as call-time kwargs to
        astream()/ainvoke() — they must be set on the client itself. Building a
        fresh client per call is cheap (same reasoning as GroqProvider: it's just
        configuring an HTTP client, not a network round-trip).
        """
        kwargs: dict = dict(
            google_api_key=self._api_key,
            model=self._model,
            temperature=temperature,
            max_output_tokens=max_tokens,
            safety_settings=_SAFETY_SETTINGS,
            timeout=_REQUEST_TIMEOUT,
        )
        if json_mode:
            kwargs["model_kwargs"] = {"response_mime_type": "application/json"}
        return ChatGoogleGenerativeAI(**kwargs)

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
        client    = self._make_client(temperature, max_tokens)

        for attempt in range(_MAX_RETRIES):
            try:
                async for chunk in client.astream(messages):
                    if chunk.content:
                        yield chunk.content
                        if do_stream:
                            await _push_token(sid, chunk.content)
                return

            except Exception as exc:
                if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "GeminiProvider: rate limit (attempt %d/%d) — retrying in %ds | %s",
                        attempt + 1, _MAX_RETRIES, wait, exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("GeminiProvider: generate failed — %s", exc)
                    return

    def get_chat_model(self):
        """Return ChatGoogleGenerativeAI for the MCP tool-use loop (bind_tools)."""
        return self._tool_model

    def get_model_name(self) -> str:
        return self._model

    def get_model_window(self) -> int:
        return _MODEL_WINDOWS.get(self._model, 1_048_576)

    async def generate_text(
        self,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
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
        """Native JSON mode — Gemini guarantees valid JSON, no regex needed."""
        messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
        try:
            client = self._make_client(temperature, max_tokens, json_mode=True)
            resp   = await client.ainvoke(messages)
            text   = resp.content or ""
            if not text.strip():
                return LLMResponse(text="", model=self._model, is_empty=True, parse_error=True)

            import json
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                from backend.agents.base_agent import parse_json_block
                data = parse_json_block(text)

            return LLMResponse(
                text=text,
                model=self._model,
                structured=data,
                parse_error=not bool(data),
            )

        except Exception as exc:
            logger.warning("GeminiProvider: JSON mode failed (%s) — falling back to text parse", exc)
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
LLMFactory.register("gemini", GeminiProvider)
