"""
backend/memory/session_store.py

Dual-backend conversation session store.

  DATABASE_URL not set (or empty)  →  SQLite  (default, demo mode)
  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db  →  PostgreSQL (production)

The public async interface is identical in both modes so no caller changes are needed
when switching backends — just set DATABASE_URL and restart.

Schema (same in both backends):
  conversation_turns(
      id          SERIAL / AUTOINCREMENT PRIMARY KEY,
      session_id  TEXT NOT NULL,
      user_id     TEXT NOT NULL,
      user_role   TEXT NOT NULL,
      project_id  TEXT NOT NULL DEFAULT '',
      query       TEXT NOT NULL,
      response    TEXT NOT NULL,
      created_at  TEXT NOT NULL
  )

Why two separate implementations instead of an ORM?
  Raw SQL keeps the schema transparent, migration scripts simple, and avoids
  pulling in SQLAlchemy as a dependency for a 6-column table. Both backends
  share exactly the same SQL strings — only the driver differs.
"""
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.core.settings import settings

logger = logging.getLogger(__name__)

_SQLITE_PATH = (
    Path(settings.SQLITE_PATH)
    if settings.SQLITE_PATH
    else Path(__file__).parents[2] / "data" / "sessions.db"
)

# ── Shared SQL ────────────────────────────────────────────────────────────────

# SQLite uses ? placeholders; PostgreSQL uses $1 $2 ... — substituted at runtime
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id          {pk},
    session_id  TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    user_role   TEXT    NOT NULL,
    project_id  TEXT    NOT NULL DEFAULT '',
    query       TEXT    NOT NULL,
    response    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
)
"""
_CREATE_IDX_SESSION = """
CREATE INDEX IF NOT EXISTS idx_session_created
    ON conversation_turns (session_id, created_at)
"""
_CREATE_IDX_PROJECT = """
CREATE INDEX IF NOT EXISTS idx_project_created
    ON conversation_turns (project_id, created_at)
"""
_MIGRATE_ADD_PROJECT = """
ALTER TABLE conversation_turns ADD COLUMN project_id TEXT NOT NULL DEFAULT ''
"""


# ── SQLite backend ────────────────────────────────────────────────────────────

class _SQLiteBackend:
    """Sync SQLite backend wrapped in asyncio.to_thread() for non-blocking calls."""

    def _connect(self):
        return sqlite3.connect(str(_SQLITE_PATH))

    def setup(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_CREATE_TABLE.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT"))
                conn.execute(_CREATE_IDX_SESSION)
                try:
                    conn.execute(_MIGRATE_ADD_PROJECT)
                    logger.info("SQLiteBackend: migrated — added project_id column")
                except sqlite3.OperationalError:
                    pass   # column already exists
                try:
                    conn.execute(_CREATE_IDX_PROJECT)
                except sqlite3.OperationalError:
                    pass   # index already exists
                conn.commit()
            logger.info("SQLiteBackend: ready at %s", _SQLITE_PATH)
        except Exception:
            logger.exception("SQLiteBackend: setup failed")

    def _save(self, session_id, user_id, user_role, query, response, project_id) -> None:
        if not session_id or not query or not response:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversation_turns "
                "(session_id, user_id, user_role, project_id, query, response, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, user_id, user_role, project_id, query, response[:2000], datetime.now().isoformat()),
            )
            conn.commit()

    def _load(self, session_id, limit, response_truncate) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, user_id, user_role, project_id, query, response, created_at "
                "FROM conversation_turns WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        turns = [dict(r) for r in reversed(rows)]
        if response_truncate > 0:
            for t in turns:
                t["response"] = t["response"][:response_truncate]
        return turns

    def _load_project(self, project_id, limit, response_truncate) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, user_id, user_role, project_id, query, response, created_at "
                "FROM conversation_turns WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        turns = [dict(r) for r in reversed(rows)]
        if response_truncate > 0:
            for t in turns:
                t["response"] = t["response"][:response_truncate]
        return turns

    async def asave(self, session_id, user_id, user_role, query, response, project_id) -> None:
        await asyncio.to_thread(self._save, session_id, user_id, user_role, query, response, project_id)

    async def aload(self, session_id, limit, response_truncate) -> list[dict]:
        return await asyncio.to_thread(self._load, session_id, limit, response_truncate)

    async def aload_project(self, project_id, limit, response_truncate) -> list[dict]:
        return await asyncio.to_thread(self._load_project, project_id, limit, response_truncate)


# ── PostgreSQL backend ────────────────────────────────────────────────────────

class _PostgresBackend:
    """
    asyncpg-backed PostgreSQL session store.

    Uses a connection pool (min 2, max 10) created once at startup.
    All operations use $1 $2 ... positional placeholders (asyncpg style).
    """

    def __init__(self, dsn: str) -> None:
        # Strip the SQLAlchemy dialect prefix so asyncpg gets a bare DSN
        self._dsn  = dsn.replace("postgresql+asyncpg://", "postgresql://")
        self._pool = None

    def setup(self) -> None:
        # Synchronous setup is not possible with asyncpg — pool is created in async setup()
        logger.info("PostgresBackend: will create pool on first async call (DSN configured)")

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=2,
                max_size=10,
                command_timeout=30,
            )
            await self._init_schema()
            logger.info("PostgresBackend: pool ready")
        return self._pool

    async def _init_schema(self) -> None:
        pool = self._pool
        async with pool.acquire() as conn:
            await conn.execute(
                _CREATE_TABLE.format(pk="SERIAL PRIMARY KEY")
            )
            await conn.execute(_CREATE_IDX_SESSION)
            await conn.execute(_CREATE_IDX_PROJECT)
            # Migration: add project_id if missing (idempotent via DO block)
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='conversation_turns' AND column_name='project_id'
                    ) THEN
                        ALTER TABLE conversation_turns
                        ADD COLUMN project_id TEXT NOT NULL DEFAULT '';
                    END IF;
                END $$;
            """)
        logger.info("PostgresBackend: schema ready")

    async def asave(self, session_id, user_id, user_role, query, response, project_id) -> None:
        if not session_id or not query or not response:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversation_turns "
                "(session_id, user_id, user_role, project_id, query, response, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                session_id, user_id, user_role, project_id,
                query, response[:2000], datetime.now().isoformat(),
            )

    async def aload(self, session_id, limit, response_truncate) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT session_id, user_id, user_role, project_id, query, response, created_at "
                "FROM conversation_turns WHERE session_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                session_id, limit,
            )
        turns = [dict(r) for r in reversed(rows)]
        if response_truncate > 0:
            for t in turns:
                t["response"] = t["response"][:response_truncate]
        return turns

    async def aload_project(self, project_id, limit, response_truncate) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT session_id, user_id, user_role, project_id, query, response, created_at "
                "FROM conversation_turns WHERE project_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                project_id, limit,
            )
        turns = [dict(r) for r in reversed(rows)]
        if response_truncate > 0:
            for t in turns:
                t["response"] = t["response"][:response_truncate]
        return turns


# ── Public SessionStore facade ────────────────────────────────────────────────

class SessionStore:
    """
    Backend-agnostic session store.

    Selects SQLite or PostgreSQL at startup based on DATABASE_URL:
        ""  or unset            → SQLite  (data/sessions.db)
        "postgresql+asyncpg://…"→ PostgreSQL (production)

    All public methods are async — callers never know which backend is active.
    The synchronous save_turn / load_recent_turns aliases remain for any
    non-async callers (e.g. admin scripts), wrapping the async path via asyncio.run().
    """

    def __init__(self) -> None:
        db_url = settings.DATABASE_URL or ""
        if db_url.startswith("postgresql"):
            self._backend = _PostgresBackend(db_url)
            logger.info("SessionStore: using PostgreSQL backend")
        else:
            self._backend = _SQLiteBackend()
            logger.info("SessionStore: using SQLite backend")
        self._backend.setup()

    # ── Primary async API ─────────────────────────────────────────────────────

    async def asave_turn(
        self,
        session_id: str,
        user_id:    str,
        user_role:  str,
        query:      str,
        response:   str,
        project_id: str = "",
    ) -> None:
        """Persist one conversation turn (non-blocking)."""
        try:
            await self._backend.asave(session_id, user_id, user_role, query, response, project_id)
            logger.debug("SessionStore.asave_turn: session='%s'", session_id)
        except Exception:
            logger.exception("SessionStore.asave_turn: failed — turn not persisted")

    async def aload_recent_turns(
        self,
        session_id:        str,
        limit:             int = 5,
        response_truncate: int = 300,
    ) -> list[dict]:
        """Return the last `limit` turns for a session, oldest first."""
        if not session_id:
            return []
        try:
            turns = await self._backend.aload(session_id, limit, response_truncate)
            logger.debug("SessionStore.aload_recent_turns: session='%s' → %d turns", session_id, len(turns))
            return turns
        except Exception:
            logger.exception("SessionStore.aload_recent_turns: failed — returning empty")
            return []

    async def aload_project_turns(
        self,
        project_id:        str,
        limit:             int = 20,
        response_truncate: int = 300,
    ) -> list[dict]:
        """Return the last `limit` turns across ALL sessions for a project."""
        if not project_id:
            return []
        try:
            return await self._backend.aload_project(project_id, limit, response_truncate)
        except Exception:
            logger.exception("SessionStore.aload_project_turns: failed — returning empty")
            return []

    # ── Sync aliases (backward-compat for admin scripts / sync callers) ───────

    def save_turn(self, session_id, user_id, user_role, query, response, project_id="") -> None:
        try:
            asyncio.get_event_loop().run_until_complete(
                self.asave_turn(session_id, user_id, user_role, query, response, project_id)
            )
        except RuntimeError:
            # Already inside a running event loop (e.g. Jupyter / Streamlit)
            # Fire-and-forget via to_thread is not available here; use sync SQLite directly
            if isinstance(self._backend, _SQLiteBackend):
                self._backend._save(session_id, user_id, user_role, query, response, project_id)

    def load_recent_turns(self, session_id, limit=5, response_truncate=300) -> list[dict]:
        try:
            return asyncio.get_event_loop().run_until_complete(
                self.aload_recent_turns(session_id, limit, response_truncate)
            )
        except RuntimeError:
            if isinstance(self._backend, _SQLiteBackend):
                return self._backend._load(session_id, limit, response_truncate)
            return []

    def load_project_turns(self, project_id, limit=20, response_truncate=300) -> list[dict]:
        try:
            return asyncio.get_event_loop().run_until_complete(
                self.aload_project_turns(project_id, limit, response_truncate)
            )
        except RuntimeError:
            if isinstance(self._backend, _SQLiteBackend):
                return self._backend._load_project(project_id, limit, response_truncate)
            return []


# ── Module-level singleton ────────────────────────────────────────────────────
session_store = SessionStore()
