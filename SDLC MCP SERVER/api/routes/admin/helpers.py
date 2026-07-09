"""
backend/api/routes/admin/helpers.py

Private stat/count helper functions used by admin endpoints.

These are infrastructure queries — they read directly from Qdrant, Redis, and SQLite
to collect system health numbers. They are NOT business logic and have no side effects.

Keeping them here means the router module stays focused on HTTP concerns only.
"""
import logging
import sqlite3
from pathlib import Path

from backend.core.settings import settings

logger = logging.getLogger(__name__)


def qdrant_chunk_count() -> int:
    """Return total vectors in the Qdrant collection, or -1 on error."""
    try:
        from backend.rag.vector_store import VectorStore
        return VectorStore().count()
    except Exception:
        logger.exception("admin/stats: qdrant count failed")
        return -1


def qdrant_collection_name() -> str:
    """Return the name of the active Qdrant collection."""
    try:
        from backend.rag.vector_store import VectorStore
        return VectorStore().collection
    except Exception:
        return settings.QDRANT_COLLECTION


def redis_key_count() -> int:
    """Return total Redis keys, or -1 on error."""
    try:
        import redis
        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        return r.dbsize()
    except Exception:
        return -1


def session_turn_count() -> int:
    """Return total rows in conversation_turns (SQLite only)."""
    try:
        db_path = Path(__file__).parents[4] / "data" / "sessions.db"
        if not db_path.exists():
            return 0
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()
            return row[0] if row else 0
    except Exception:
        return -1


def semantic_fact_count() -> int:
    """Return total facts stored in the semantic_memory Qdrant collection."""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=settings.QDRANT_URL)
        info = client.get_collection("semantic_memory")
        return info.points_count or 0
    except Exception:
        return 0
