"""
Row-Level Security tests — the single highest-blast-radius guarantee in the
whole platform (§Z risk register: "multi-tenant data leakage from an
app-layer authz bug" -> critical impact). These deliberately query WITHOUT
any tenant_id filter in the Python code, proving isolation is enforced by
Postgres itself, not by every call site remembering a WHERE clause.
"""
from __future__ import annotations

from sqlalchemy import select

from backend.app.db.models import User
from backend.app.db.session import get_db_session, tenant_scoped_session


# ── Positive ──────────────────────────────────────────────────────────────

async def test_scoped_session_sees_own_tenant_users(make_tenant):
    tenant = await make_tenant()

    async with tenant_scoped_session(tenant.tenant_id) as session:
        rows = (await session.execute(select(User))).scalars().all()

    assert len(rows) == 1
    assert rows[0].email == tenant.admin_email


# ── Negative (the critical one) ───────────────────────────────────────────

async def test_scoped_session_cannot_see_another_tenants_users(make_tenant):
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()

    async with tenant_scoped_session(tenant_a.tenant_id) as session:
        # No .where(User.tenant_id == ...) anywhere — if RLS were not
        # enforcing this, tenant B's user would leak into this result.
        rows = (await session.execute(select(User))).scalars().all()

    emails = {u.email for u in rows}
    assert tenant_a.admin_email in emails
    assert tenant_b.admin_email not in emails
    assert len(rows) == 1


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_unscoped_session_sees_zero_rows_not_every_row(make_tenant):
    """
    Fail-safe default: no tenant context set at all must mean "see nothing",
    never "see everything" — the dangerous failure mode this guards against.
    """
    await make_tenant()
    await make_tenant()

    async with get_db_session() as session:
        rows = (await session.execute(select(User))).scalars().all()

    assert rows == []


# ── Side effects ────────────────────────────────────────────────────────

async def test_tenant_context_does_not_leak_across_sequential_sessions(make_tenant):
    """
    SET LOCAL / set_config(..., true) is transaction-scoped. On a pooled
    connection, a later session reusing the same physical connection must
    not inherit an earlier session's tenant context.
    """
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()

    async with tenant_scoped_session(tenant_a.tenant_id) as session:
        rows_a = (await session.execute(select(User))).scalars().all()

    async with tenant_scoped_session(tenant_b.tenant_id) as session:
        rows_b = (await session.execute(select(User))).scalars().all()

    assert {u.email for u in rows_a} == {tenant_a.admin_email}
    assert {u.email for u in rows_b} == {tenant_b.admin_email}
