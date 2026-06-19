# Database Standards — AI SDLC Assistant

Rules for all database operations using SQLAlchemy with SQLite (demo) / PostgreSQL (production).

---

## Database Choice

| Environment | Database | Driver |
|-------------|----------|--------|
| Demo / local | SQLite | `aiosqlite` (async) |
| Production (v2) | PostgreSQL | `asyncpg` |

The schema and ORM code are identical — only the connection string changes. Design for PostgreSQL compatibility even when using SQLite.

---

## SQLAlchemy — Always Use ORM, Never Raw SQL

```python
# CORRECT — SQLAlchemy ORM
async with AsyncSession(engine) as session:
    result = await session.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    messages = result.scalars().all()

# WRONG — raw SQL string
cursor.execute("SELECT * FROM messages WHERE session_id = ?", (session_id,))
```

**Why**: Raw SQL is fragile (SQL injection risk, no type safety, breaks on DB change). ORM is validated, typed, and portable.

---

## Models — All in backend/memory/models.py

All SQLAlchemy table models live in a single file: `backend/memory/models.py`. Never define models inline in service files.

```python
# backend/memory/models.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, Boolean, Text
from datetime import datetime
import uuid

class Base(DeclarativeBase):
    pass

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(50))
    project: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(20))      # "user" | "assistant" | "system"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

class SessionSummary(Base):
    __tablename__ = "session_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(Text)
    turn_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

---

## Table Naming Conventions

- Snake case, plural: `sessions`, `messages`, `session_summaries`, `episodic_events`
- Foreign key columns: `{table_singular}_id` (e.g. `session_id` references `sessions.id`)
- Timestamp columns always named `created_at`, `updated_at`, `last_active_at`
- UUID primary keys (string, not integer) — portable across SQLite and PostgreSQL

---

## Async Session Pattern

Always use `AsyncSession` as an async context manager. Never leave sessions open.

```python
# CORRECT — context manager ensures session is closed
async def save_message(session_id: str, role: str, content: str) -> None:
    async with AsyncSession(engine) as session:
        async with session.begin():
            msg = Message(session_id=session_id, role=role, content=content)
            session.add(msg)
        # commit happens automatically on exiting session.begin()

# WRONG — session not properly closed
session = AsyncSession(engine)
session.add(msg)
await session.commit()
# session leaks if an exception occurs before this point
```

---

## Engine — Create Once at Startup

Create the async engine once at application startup (FastAPI lifespan). Never create per-request.

```python
# backend/memory/session_store.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Created once — reused for all requests
engine = create_async_engine(
    settings.DATABASE_URL,   # from .env: "sqlite+aiosqlite:///./data/sdlc.db"
    echo=False,              # set True for SQL debug logging only
)

async def init_db():
    """Create tables if they don't exist. Call once at startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

---

## Database URL

Set in `.env`:
```
DATABASE_URL=sqlite+aiosqlite:///./data/sdlc.db
```

For PostgreSQL (future):
```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/sdlc
```

SQLAlchemy handles both. No code changes needed when switching.

---

## Querying Rules

- Use `select()` statements, not `query()` (legacy ORM style).
- Always use parameterized conditions: `.where(Model.field == value)` — SQLAlchemy escapes for you.
- Limit results when fetching lists: `.limit(100)` — never return unbounded queries.
- Use `.order_by(Model.created_at.desc())` for time-sorted results.

---

## No Migrations Needed for Demo — But Design for Them

For the demo, `Base.metadata.create_all(engine)` at startup creates tables if they don't exist.

When moving to production (v2), use Alembic for schema migrations:
- Never alter production tables manually
- Every schema change = one Alembic migration file
- Design columns with future-proofing: use `nullable=True` for columns that might not exist in v1 data

---

## What NOT to Put in the Database

- LLM prompts (those are in `config/prompts.yaml`)
- Qdrant vector data (Qdrant handles its own persistence)
- Redis cached data (Redis handles its own persistence)
- Raw file contents / document text (store in `data/` filesystem + Qdrant)
- API keys or secrets (store in `.env` only)
