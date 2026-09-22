"""
End-to-end auth/RBAC/tenancy tests through the real HTTP API — login, RBAC
enforcement, and the cross-tenant-access test that matches the blueprint's
own §V auth scenario table line for line: a valid token for tenant A against
tenant B's resource ID must 404, never 403 (no existence confirmation) and
never leak data.

Uses httpx.AsyncClient + ASGITransport rather than Starlette's sync
TestClient deliberately: TestClient runs the ASGI app on a separate thread
with its own event loop (an anyio portal), which is a different loop than
the one this test function and its DB fixtures run on. The module-level
asyncpg connection pool in backend/app/db/session.py is loop-bound, so a
connection opened by one loop and reused by another raises "Event loop is
closed". AsyncClient keeps the whole test — fixture, API call, and app code
— on the single session-scoped loop configured in pyproject.toml.
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from backend.app.main import app

_TRANSPORT = ASGITransport(app=app)


async def _client() -> AsyncClient:
    return AsyncClient(transport=_TRANSPORT, base_url="http://testserver")


async def _login(client: AsyncClient, slug: str, email: str, password: str) -> str:
    response = await client.post("/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Positive ──────────────────────────────────────────────────────────────

async def test_login_then_get_own_profile(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/users/me", headers=_auth_header(token))

    assert response.status_code == 200
    assert response.json()["email"] == tenant.admin_email


async def test_admin_with_permission_lists_own_tenant_users(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get(f"/v1/tenants/{tenant.tenant_id}/users", headers=_auth_header(token))

    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {tenant.admin_email}


# ── Negative ──────────────────────────────────────────────────────────────

async def test_login_wrong_password_is_rejected(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        response = await client.post(
            "/v1/auth/login", json={"tenant_slug": tenant.slug, "email": tenant.admin_email, "password": "wrong"}
        )
    assert response.status_code == 401
    assert "wrong" not in response.json()["detail"].lower()  # no confirmation the password itself was "wrong-shaped"


async def test_login_unknown_tenant_slug_is_rejected():
    async with await _client() as client:
        response = await client.post(
            "/v1/auth/login", json={"tenant_slug": "does-not-exist", "email": "a@b.com", "password": "x"}
        )
    assert response.status_code == 401


async def test_user_without_permission_gets_403(make_tenant):
    tenant = await make_tenant(permissions=[])  # no users:read
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get(f"/v1/tenants/{tenant.tenant_id}/users", headers=_auth_header(token))

    assert response.status_code == 403


# ── Edge (the critical isolation scenario) ────────────────────────────────

async def test_valid_token_for_tenant_a_against_tenant_b_resource_is_404_not_leak(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        response = await client.get(f"/v1/tenants/{tenant_b.tenant_id}/users", headers=_auth_header(token_a))

    # Not 403 (that would confirm tenant_b's ID refers to a real resource)
    # and the body must never contain tenant B's admin email under any status.
    assert response.status_code == 404
    assert tenant_b.admin_email not in response.text


async def test_missing_authorization_header_is_401():
    async with await _client() as client:
        response = await client.get("/v1/users/me")
    assert response.status_code == 401


async def test_malformed_bearer_token_is_401():
    async with await _client() as client:
        response = await client.get("/v1/users/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert response.status_code == 401


async def test_deleted_user_with_a_still_valid_token_is_rejected(make_tenant):
    """
    A JWT has no server-side revocation check beyond signature + expiry — if
    an account is deleted mid-session, the token itself stays cryptographically
    valid. The NEXT request with it must still fail closed (401), not
    silently succeed against a user row that no longer exists. Exercises the
    identical `if user is None: raise 401` guard present in both
    tenant_routes.py's get_me and memory_routes.py's set_memory_consent.
    """
    from sqlalchemy import delete

    from backend.app.db.models import User
    from backend.app.db.session import tenant_scoped_session

    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)

        async with tenant_scoped_session(tenant.tenant_id) as session:
            await session.execute(delete(User).where(User.email == tenant.admin_email))

        me_response = await client.get("/v1/users/me", headers=_auth_header(token))
        consent_response = await client.post(
            "/v1/me/memory-consent", json={"enabled": True}, headers=_auth_header(token)
        )

    assert me_response.status_code == 401
    assert consent_response.status_code == 401


# ── Side effects ────────────────────────────────────────────────────────

async def test_two_tenants_logging_in_back_to_back_never_cross_contaminate(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)

        response_a = await client.get(f"/v1/tenants/{tenant_a.tenant_id}/users", headers=_auth_header(token_a))
        response_b = await client.get(f"/v1/tenants/{tenant_b.tenant_id}/users", headers=_auth_header(token_b))

    users_a = response_a.json()
    users_b = response_b.json()
    assert {u["email"] for u in users_a} == {tenant_a.admin_email}
    assert {u["email"] for u in users_b} == {tenant_b.admin_email}
