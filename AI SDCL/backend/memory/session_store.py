"""
backend/memory/session_store.py

SQLite-backed conversation session store.

Each Q&A turn is saved here immediately after the graph finishes.
The retrieve_memory node loads recent turns at the START of the next request,
so the LLM can reference "as I mentioned before..." or understand follow-up questions.

Why SQLite (not Redis for sessions)?
  Redis is fast but volatile — data can be lost on restart (depending on config).
  Conversation history needs to survive server restarts for a coherent user experience.
  SQLite is a local file — zero infrastructure, always available, persists forever.
  Redis (session TTL 24h) handles the "active session summary" use case in a later phase.

Schema:
  conversation_turns(id, session_id, user_id, user_role, query, response, created_at)

One row per user turn. We load the last 5 turns for a session when building context.
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Database file — stored in data/ alongside mock documents
_DB_PATH = Path(__file__).parents[2] / "data" / "sessions.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    user_role   TEXT    NOT NULL,
    query       TEXT    NOT NULL,
    response    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_session_created
    ON conversation_turns (session_id, created_at)
"""


class SessionStore:
    """
    Synchronous SQLite-backed session store.

    Why synchronous?
      sqlite3 is a synchronous library. Wrapping it in asyncio.to_thread() adds
      complexity without benefit for a single-user demo. If this were a multi-user
      production system, we'd use aiosqlite or PostgreSQL with asyncpg.

    Each method opens and closes its own connection — no shared connection state.
    SQLite handles concurrent reads/writes safely at this scale.
    """

    def __init__(self) -> None:
        self._init_db()

    def _init_db(self) -> None:
        """Create the table and index if they don't exist yet."""
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.execute(_CREATE_TABLE_SQL)
                conn.execute(_CREATE_INDEX_SQL)
                conn.commit()
            logger.info("SessionStore: database ready at %s", _DB_PATH)
        except Exception:
            logger.exception("SessionStore: failed to initialise database at %s", _DB_PATH)

    def save_turn(
        self,
        session_id: str,
        user_id:    str,
        user_role:  str,
        query:      str,
        response:   str,
    ) -> None:
        """
        Persist one conversation turn.

        Called by chat.py after the graph returns a response.
        On any error: logs and continues — never let persistence failure break the response.
        """
        if not session_id or not query or not response:
            return
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO conversation_turns "
                    "(session_id, user_id, user_role, query, response, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, user_id, user_role, query, response[:2000], datetime.now().isoformat()),
                )
                conn.commit()
            logger.debug("SessionStore: saved turn for session='%s'", session_id)
        except Exception:
            logger.exception("SessionStore.save_turn: error — turn not persisted")

    def load_recent_turns(self, session_id: str, limit: int = 5) -> list[dict]:
        """
        Return the last `limit` turns for a session, in chronological order.

        Returns an empty list if the session has no history or on any error.
        The response field is truncated to 300 chars — enough for context, not so much
        that it consumes the whole token budget.
        """
        if not session_id:
            return []
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT session_id, user_id, user_role, query, response, created_at "
                    "FROM conversation_turns "
                    "WHERE session_id = ? "
                    "ORDER BY created_at DESC "
                    "LIMIT ?",
                    (session_id, limit),
                )
                rows = cursor.fetchall()

            # Reverse so oldest turn is first (natural reading order for the LLM)
            turns = [dict(row) for row in reversed(rows)]

            # Truncate responses — we don't want old full responses eating the token budget
            for t in turns:
                t["response"] = t["response"][:300]

            logger.debug(
                "SessionStore.load_recent_turns: session='%s' → %d turns",
                session_id, len(turns),
            )
            return turns

        except Exception:
            logger.exception("SessionStore.load_recent_turns: error — returning empty history")
            return []


# ── Module-level singleton ────────────────────────────────────────────────────
session_store = SessionStore()
