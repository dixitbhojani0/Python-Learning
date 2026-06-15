"""InMemoryMemory — sliding-window conversation buffer stored in process memory.

Activate by setting memory_enabled=true in .env.
Window size is controlled by memory_max_turns (default 5).

For production, swap this with a Redis or database-backed implementation
by registering a new class in _MEMORY_REGISTRY in component_factory.py.
"""

import logging
from collections import deque
from core.interfaces import BaseMemory
from core.models import ConversationTurn

logger = logging.getLogger(__name__)


class InMemoryMemory(BaseMemory):
    """In-process sliding-window memory — no persistence across restarts."""

    def __init__(self, max_turns: int = 5) -> None:
        self._max_turns = max_turns
        self._turns: deque[ConversationTurn] = deque(maxlen=max_turns)

    def add_turn(self, question: str, answer: str) -> None:
        self._turns.append(ConversationTurn(question=question, answer=answer))
        logger.debug("Memory: stored turn #%d", len(self._turns))

    def get_history(self) -> list[dict]:
        msgs: list[dict] = []
        for t in self._turns:
            msgs.append({"role": "user",      "content": t.question})
            msgs.append({"role": "assistant", "content": t.answer})
        return msgs

    def get_turns(self) -> list[ConversationTurn]:
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()
        logger.debug("Memory: cleared")

    def __len__(self) -> int:
        return len(self._turns)
