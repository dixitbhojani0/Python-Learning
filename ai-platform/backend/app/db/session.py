"""
backend/app/db/session.py

Two session flavors, deliberately different:

  get_db_session()              — no tenant context set. Only safe to use
                                   against tables that are NOT RLS-scoped
                                   (today: only `tenants` itself — you can't
                                   look up which tenant you're in before you
                                   know which tenant you're in).

  tenant_scoped_session(id)     — sets `app.current_tenant_id` for the
                                   Postgres session via SET LOCAL (scoped to
                                   the transaction, cleared automatically on
                                   commit/rollback — never leaks to the next
                                   request on a pooled connection). Every
                                   RLS-protected table read/write must go
                                   through this.

If a query against an RLS-protected table runs without the tenant context
set, the RLS policy's `current_setting(..., true)` returns NULL, no row's
tenant_id equals NULL, and the query returns zero rows — fail-safe by
construction, not by application-code discipline (§J).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.core.settings import settings

# ponytail: NullPool means a fresh physical connection per checkout (no
# connection is ever reused across an event-loop boundary) — a real
# per-request TCP/TLS-handshake cost at high QPS. Upgrade to a pooled
# engine once load testing actually shows that cost matters; the test
# suite hitting a Windows ProactorEventLoop + asyncpg + httpx/anyio
# interaction that periodically kills a pooled connection's bound loop is
# reason enough to not pay pooling's complexity before it earns its keep.
engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
_SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with _SessionLocal() as session:
        yield session


@asynccontextmanager
async def tenant_scoped_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with _SessionLocal() as session:
        async with session.begin():
            # set_config(..., true) = transaction-local (equivalent to SET LOCAL,
            # but works with asyncpg's parameter binding where SET LOCAL itself
            # does not accept bound parameters).
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            yield session
