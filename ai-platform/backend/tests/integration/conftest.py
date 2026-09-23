"""
Fixtures for Phase 2 integration tests — each test gets its own freshly
created tenant(s) with a unique slug/email (uuid-suffixed) so tests never
collide or depend on run order, and each created tenant is torn down
afterward so repeated full-suite runs don't accumulate rows forever.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from backend.app.core.security import hash_password
from backend.app.db.models import (
    Chunk,
    Conversation,
    Document,
    IngestionJob,
    Message,
    PendingToolApproval,
    Role,
    Tenant,
    TelemetryEvent,
    User,
    UserMemory,
)
from backend.app.db.session import get_db_session, tenant_scoped_session


@dataclass
class SeededTenant:
    tenant_id: uuid.UUID
    slug: str
    admin_email: str
    admin_password: str
    role_permissions: list[str]


@pytest.fixture
async def make_tenant():
    """
    Usage: tenant = await make_tenant(permissions=["users:read"])
    Creates a tenant + one role + one user; deletes all three after the test.
    """
    created: list[uuid.UUID] = []

    async def _make(permissions: list[str] | None = None) -> SeededTenant:
        suffix = uuid.uuid4().hex[:8]
        slug = f"test-{suffix}"
        email = f"admin-{suffix}@test.local"
        password = "correct-horse-battery-staple"
        permissions = permissions if permissions is not None else ["users:read", "users:write"]

        async with get_db_session() as session:
            async with session.begin():
                tenant = Tenant(id=uuid.uuid4(), slug=slug, name=f"Test Tenant {suffix}")
                session.add(tenant)

        async with tenant_scoped_session(tenant.id) as session:
            role = Role(id=uuid.uuid4(), tenant_id=tenant.id, name="admin", permissions=permissions)
            session.add(role)
            await session.flush()
            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                email=email,
                hashed_password=hash_password(password),
                role_id=role.id,
            )
            session.add(user)

        created.append(tenant.id)
        return SeededTenant(
            tenant_id=tenant.id, slug=slug, admin_email=email, admin_password=password, role_permissions=permissions
        )

    yield _make

    for tenant_id in created:
        async with tenant_scoped_session(tenant_id) as session:
            # FK order matters: chunks -> documents, messages -> conversations,
            # then users -> roles. Documents/chunks don't reference users, so
            # they can be deleted in any order relative to that pair — but
            # both groups must clear before the tenant row itself.
            # PendingToolApproval references users + conversations — clear
            # before either of those. IngestionJob references documents —
            # clear before Document, same reasoning as Chunk.
            await session.execute(delete(PendingToolApproval).where(PendingToolApproval.tenant_id == tenant_id))
            await session.execute(delete(IngestionJob).where(IngestionJob.tenant_id == tenant_id))
            await session.execute(delete(Chunk).where(Chunk.tenant_id == tenant_id))
            await session.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await session.execute(delete(UserMemory).where(UserMemory.tenant_id == tenant_id))
            await session.execute(delete(TelemetryEvent).where(TelemetryEvent.tenant_id == tenant_id))
            await session.execute(delete(Message).where(Message.tenant_id == tenant_id))
            await session.execute(delete(Conversation).where(Conversation.tenant_id == tenant_id))
            await session.execute(delete(User).where(User.tenant_id == tenant_id))
            await session.execute(delete(Role).where(Role.tenant_id == tenant_id))
        async with get_db_session() as session:
            async with session.begin():
                await session.execute(delete(Tenant).where(Tenant.id == tenant_id))


@dataclass
class SeededUser:
    tenant_id: uuid.UUID
    email: str
    password: str


@pytest.fixture
async def add_user_to_tenant():
    """
    A second user in an already-seeded tenant — for tests proving user-level
    isolation WITHIN one tenant (something RLS, being tenant-scoped only,
    cannot enforce; see api/chat_routes.py's explicit user_id checks).
    Cleanup rides on make_tenant's teardown (it deletes all users for the
    tenant_id, not just the one it created), so no separate teardown needed
    here — but this fixture must be used together with make_tenant in the
    same test.
    """

    async def _make(tenant: SeededTenant, permissions: list[str] | None = None) -> SeededUser:
        suffix = uuid.uuid4().hex[:8]
        email = f"user-{suffix}@test.local"
        password = "correct-horse-battery-staple-2"
        permissions = permissions if permissions is not None else tenant.role_permissions

        async with tenant_scoped_session(tenant.tenant_id) as session:
            role = Role(id=uuid.uuid4(), tenant_id=tenant.tenant_id, name="member", permissions=permissions)
            session.add(role)
            await session.flush()
            session.add(
                User(
                    id=uuid.uuid4(),
                    tenant_id=tenant.tenant_id,
                    email=email,
                    hashed_password=hash_password(password),
                    role_id=role.id,
                )
            )

        return SeededUser(tenant_id=tenant.tenant_id, email=email, password=password)

    return _make
