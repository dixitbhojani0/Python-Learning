"""
backend/alembic/env.py

Runs migrations against the same async engine/config the app uses (no second
DB driver just for migrations) — asyncio.run() drives the async connection
inside Alembic's normally-sync migration runner, per Alembic's documented
async cookbook pattern.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# ai-platform/ root on sys.path regardless of cwd alembic is invoked from —
# "backend" must be importable as a top-level package (same reason
# pyproject.toml sets pythonpath=["."] for pytest).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.settings import settings  # noqa: E402
from backend.app.db.base import Base  # noqa: E402
from backend.app.db.models import (  # noqa: E402,F401  (register models on Base.metadata)
    Chunk,
    Conversation,
    Document,
    Message,
    PendingToolApproval,
    Role,
    Tenant,
    TelemetryEvent,
    User,
    UserMemory,
)

config = context.config
target_metadata = Base.metadata

# Deliberately NOT backend.app.db.session's runtime `engine` — that connects
# as the restricted platform_app role, which has no DDL privileges. Migrations
# need the table-owning role (see Settings.MIGRATIONS_DATABASE_URL).
engine = create_async_engine(settings.MIGRATIONS_DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    assert isinstance(engine, AsyncEngine)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
