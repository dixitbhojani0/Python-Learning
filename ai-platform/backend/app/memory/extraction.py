"""
backend/app/memory/extraction.py

Deterministic pattern-based fact extraction — the swappable seam an
LLM-based structured-output extractor replaces later (same adapter
philosophy as §M), not a permanent design choice. A regex is honest about
what it can't do (no real NLU) in a way a mocked "LLM extraction" would
quietly pretend to have without a real key to back it.

Security note (§17 memory poisoning): this function has no opinion about
WHERE its input came from — that is a deliberate separation of concerns.
The caller (backend/app/memory/service.py, invoked from chat_routes.py)
must only ever pass the user's own directly-typed message here, never
retrieved RAG chunk content or the assistant's own generated text. Feeding
this function untrusted retrieved content would let a poisoned document
plant instructions like "remember that the admin password is X" into a
user's permanent memory — the fix is at the call site, but it's exactly the
kind of mistake worth a comment where it would first go wrong.
"""
from __future__ import annotations

import re

_PATTERN = re.compile(r"^remember(?:\s+that)?\s+(?:my\s+)?(?P<key>.+?)\s+is\s+(?P<value>.+)$", re.IGNORECASE)


def extract_memory_candidates(message: str) -> list[tuple[str, str]]:
    """
    Returns [(key, value)] for a message shaped like "remember (that) (my)
    <key> is <value>", else []. Deliberately requires the explicit word
    "remember" — extracting a fact from every incidental "my X is Y"
    sentence would be over-collection, exactly what consent gating (§14)
    exists to prevent.
    """
    match = _PATTERN.match(message.strip())
    if not match:
        return []

    key = re.sub(r"\s+", "_", match.group("key").strip().lower())
    value = match.group("value").strip().rstrip(".!")
    if not key or not value:
        return []
    return [(key, value)]
