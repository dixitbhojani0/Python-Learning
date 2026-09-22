"""
backend/scripts/seed_dev_data.py

Creates two demo tenants (proving isolation needs at least two to be
meaningful) with one admin user each. Run with:
    ../.venv/Scripts/python.exe -m scripts.seed_dev_data
"""
from __future__ import annotations

import asyncio
import uuid

from backend.app.core.security import hash_password
from backend.app.db.models import Role, Tenant, User
from backend.app.db.session import tenant_scoped_session


async def _seed_tenant(slug: str, name: str, admin_email: str, admin_password: str) -> None:
    from backend.app.db.session import get_db_session

    async with get_db_session() as session:
        async with session.begin():
            tenant = Tenant(id=uuid.uuid4(), slug=slug, name=name)
            session.add(tenant)

    async with tenant_scoped_session(tenant.id) as session:
        admin_role = Role(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name="admin",
            permissions=["users:read", "users:write", "documents:read", "documents:write", "tools:approve"],
        )
        session.add(admin_role)
        await session.flush()

        admin_user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=admin_email,
            hashed_password=hash_password(admin_password),
            role_id=admin_role.id,
        )
        session.add(admin_user)

    print(f"Seeded tenant '{slug}' with admin '{admin_email}' (tenant_id={tenant.id})")


async def main() -> None:
    await _seed_tenant("acme", "Acme Corp", "admin@acme.test", "acme-dev-password")
    await _seed_tenant("globex", "Globex Inc", "admin@globex.test", "globex-dev-password")


if __name__ == "__main__":
    asyncio.run(main())
