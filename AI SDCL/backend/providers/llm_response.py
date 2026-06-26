"""
backend/providers/llm_response.py

Normalized LLM output dataclass — the single contract between providers and agents.

Every LLM provider (Groq, Gemini, OpenAI, etc.) must convert its raw response
format into this dataclass before returning. Agents always receive LLMResponse —
they never know which provider was used or what the raw format was.

Why a dataclass and not a dict?
  - Type safety: callers know exactly which fields exist (IDE auto-completes them)
  - Immutable intent: fields are clearly named and typed
  - Adding a field later is backwards-compatible (just add with a default)

Producers: GroqProvider, GeminiProvider, OpenAIProvider (via generate_text / generate_structured)
Consumers: All agents, metrics.py, semantic_memory.py
"""
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    """
    Normalized output returned by every LLM provider method.

    Fields:
        text         — full response text (always populated from generate_text)
        structured   — parsed JSON dict (populated by generate_structured; {} on failure)
        model        — which model produced this response (for logging / LangSmith)
        is_empty     — True when LLM returned nothing (rate limit / quota exhausted)
        parse_error  — True when generate_structured could not extract valid JSON
                       (text is still available — caller decides how to handle)
    """
    text:        str  = ""
    structured:  dict = field(default_factory=dict)
    model:       str  = ""
    is_empty:    bool = False
    parse_error: bool = False

    def __bool__(self) -> bool:
        """Truthy if the response has usable content."""
        return not self.is_empty and bool(self.text.strip())
