"""
backend/app/tools/intent.py

Deterministic tool-trigger detection — the same honest choice as
memory/extraction.py: none of MockProvider/GeminiProvider/GroqProvider
implement real structured tool-calling in this codebase yet, so pretending
an LLM "decided" to call a tool would be dishonest theater. This is the
swappable seam (§M) a real function-calling integration replaces later,
without changing chat_routes.py's agent loop or the tools themselves.

Bounded autonomy, concretely: returns AT MOST one match, so a single chat
turn can never trigger more than one tool call — that's the whole "max
steps" enforcement for this phase (§15), simple because there is currently
only ever one step to take.
"""
from __future__ import annotations

import re

_CALCULATOR_PATTERN = re.compile(r"\bcalculate\s+(?P<expression>[\d\s+\-*/().]+)", re.IGNORECASE)
_TIME_PATTERN = re.compile(r"what(?:'s| is) the (?:current )?time\b|what time is it\b", re.IGNORECASE)
_DELETE_DOCS_PATTERN = re.compile(r"\bdelete all (?:my |the )?documents\b", re.IGNORECASE)


def detect_tool_intent(message: str) -> tuple[str, dict] | None:
    """Returns (tool_name, kwargs) for the first matching pattern, else None. Order is the priority order."""
    if match := _CALCULATOR_PATTERN.search(message):
        return "calculator", {"expression": match.group("expression").strip()}
    if _TIME_PATTERN.search(message):
        return "current_time", {}
    if _DELETE_DOCS_PATTERN.search(message):
        return "delete_all_documents", {}
    return None
