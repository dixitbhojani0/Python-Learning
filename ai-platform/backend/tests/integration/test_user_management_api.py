"""
Phase 15 user-management tests: role listing/creation, user listing/invite,
and role reassignment — all gated by the same "users:read"/"users:write"
permissions every other admin route uses (§30: no new permission namespace
for a second feature needing the same shape of access control).
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

async def test_list_roles_returns_the_seeded_admin_role(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/roles", headers=_auth_header(token))

    assert response.status_code == 200
    roles = response.json()
    assert len(roles) == 1
    assert roles[0]["name"] == "admin"
    assert set(roles[0]["permissions"]) == {"users:read"}


async def test_create_role_with_valid_permissions_appears_in_the_list(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create_response = await client.post(
            "/v1/admin/roles",
            json={"name": "member", "permissions": ["documents:read"]},
            headers=_auth_header(token),
        )
        list_response = await client.get("/v1/admin/roles", headers=_auth_header(token))

    assert create_response.status_code == 201
    assert create_response.json()["name"] == "member"
    names = {r["name"] for r in list_response.json()}
    assert names == {"admin", "member"}


async def test_list_users_reports_email_and_role_name(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/users", headers=_auth_header(token))

    assert response.status_code == 200
    users = response.json()
    assert len(users) == 1
    assert users[0]["email"] == tenant.admin_email
    assert users[0]["role_name"] == "admin"


async def test_invite_user_creates_an_account_that_can_actually_log_in(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        roles = (await client.get("/v1/admin/roles", headers=_auth_header(token))).json()
        role_id = roles[0]["id"]

        invite_response = await client.post(
            "/v1/admin/users",
            json={"email": "newperson@acme.test", "role_id": role_id},
            headers=_auth_header(token),
        )
        assert invite_response.status_code == 201
        temp_password = invite_response.json()["temporary_password"]

        login_response = await client.post(
            "/v1/auth/login",
            json={"tenant_slug": tenant.slug, "email": "newperson@acme.test", "password": temp_password},
        )

    assert login_response.status_code == 200


async def test_update_user_role_changes_the_users_permissions(make_tenant, add_user_to_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    member = await add_user_to_tenant(tenant, permissions=["documents:read"])

    async with await _client() as client:
        admin_token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        member_before_login = await client.post(
            "/v1/auth/login", json={"tenant_slug": tenant.slug, "email": member.email, "password": member.password}
        )
        member_token_before = member_before_login.json()["access_token"]

        users = (await client.get("/v1/admin/users", headers=_auth_header(admin_token))).json()
        member_row = next(u for u in users if u["email"] == member.email)
        admin_role_id = next(u for u in users if u["email"] == tenant.admin_email)["role_id"]

        update_response = await client.patch(
            f"/v1/admin/users/{member_row['id']}/role",
            json={"role_id": admin_role_id},
            headers=_auth_header(admin_token),
        )

        # The member's OLD token still has the old (narrower) permissions
        # baked in — proving the role change is real, not a no-op, requires
        # a FRESH login to see it take effect, the same JWT-is-a-snapshot
        # behavior this project hit for real with the seed-data fix.
        member_after_login = await client.post(
            "/v1/auth/login", json={"tenant_slug": tenant.slug, "email": member.email, "password": member.password}
        )
        member_token_after = member_after_login.json()["access_token"]
        old_token_tenant_check = await client.get("/v1/admin/tenant", headers=_auth_header(member_token_before))
        new_token_tenant_check = await client.get("/v1/admin/tenant", headers=_auth_header(member_token_after))

    assert update_response.status_code == 200
    assert update_response.json()["role_name"] == "admin"
    assert old_token_tenant_check.status_code == 403  # old token: still the narrow "member" permissions
    assert new_token_tenant_check.status_code == 200  # fresh token: now has users:read via the new role


# ── Negative ──────────────────────────────────────────────────────────────

async def test_create_role_with_an_unrecognized_permission_is_400(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/roles",
            json={"name": "bogus", "permissions": ["not:a:real:permission"]},
            headers=_auth_header(token),
        )
    assert response.status_code == 400


async def test_create_role_with_a_duplicate_name_is_400(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/roles", json={"name": "admin", "permissions": []}, headers=_auth_header(token)
        )
    assert response.status_code == 400


async def test_invite_user_with_an_email_already_in_the_tenant_is_400(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        role_id = (await client.get("/v1/admin/roles", headers=_auth_header(token))).json()[0]["id"]
        response = await client.post(
            "/v1/admin/users",
            json={"email": tenant.admin_email, "role_id": role_id},
            headers=_auth_header(token),
        )
    assert response.status_code == 400


async def test_invite_user_with_a_nonexistent_role_is_404(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/users",
            json={"email": "x@acme.test", "role_id": "00000000-0000-0000-0000-000000000000"},
            headers=_auth_header(token),
        )
    assert response.status_code == 404


async def test_update_role_of_a_nonexistent_user_is_404(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        role_id = (await client.get("/v1/admin/roles", headers=_auth_header(token))).json()[0]["id"]
        response = await client.patch(
            "/v1/admin/users/00000000-0000-0000-0000-000000000000/role",
            json={"role_id": role_id},
            headers=_auth_header(token),
        )
    assert response.status_code == 404


async def test_update_user_to_a_nonexistent_role_is_404(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        users = (await client.get("/v1/admin/users", headers=_auth_header(token))).json()
        response = await client.patch(
            f"/v1/admin/users/{users[0]['id']}/role",
            json={"role_id": "00000000-0000-0000-0000-000000000000"},
            headers=_auth_header(token),
        )
    assert response.status_code == 404


async def test_list_roles_without_users_read_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=[])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/roles", headers=_auth_header(token))
    assert response.status_code == 403


async def test_create_role_without_users_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])  # read-only
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/roles", json={"name": "member", "permissions": []}, headers=_auth_header(token)
        )
    assert response.status_code == 403


async def test_invite_user_without_users_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        role_id = (await client.get("/v1/admin/roles", headers=_auth_header(token))).json()[0]["id"]
        response = await client.post(
            "/v1/admin/users", json={"email": "x@acme.test", "role_id": role_id}, headers=_auth_header(token)
        )
    assert response.status_code == 403


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_admin_endpoints_require_auth():
    async with await _client() as client:
        assert (await client.get("/v1/admin/roles")).status_code == 401
        assert (await client.get("/v1/admin/users")).status_code == 401
        assert (await client.post("/v1/admin/users", json={"email": "x@a.com", "role_id": "00000000-0000-0000-0000-000000000000"})).status_code == 401


async def test_two_generated_temporary_passwords_are_never_the_same(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        role_id = (await client.get("/v1/admin/roles", headers=_auth_header(token))).json()[0]["id"]
        first = await client.post(
            "/v1/admin/users", json={"email": "one@acme.test", "role_id": role_id}, headers=_auth_header(token)
        )
        second = await client.post(
            "/v1/admin/users", json={"email": "two@acme.test", "role_id": role_id}, headers=_auth_header(token)
        )
    assert first.json()["temporary_password"] != second.json()["temporary_password"]


# ── Side effects (tenant isolation) ─────────────────────────────────────

async def test_list_users_never_shows_another_tenants_users(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.get("/v1/admin/users", headers=_auth_header(token_b))

    emails = {u["email"] for u in response.json()}
    assert tenant_a.admin_email not in emails


async def test_cannot_invite_a_user_using_another_tenants_role_id(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read", "users:write"])
    tenant_b = await make_tenant(permissions=["users:read", "users:write"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        role_a_id = (await client.get("/v1/admin/roles", headers=_auth_header(token_a))).json()[0]["id"]

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.post(
            "/v1/admin/users", json={"email": "x@b.test", "role_id": role_a_id}, headers=_auth_header(token_b)
        )

    # RLS makes tenant A's role invisible to tenant B's scoped session —
    # 404, not a cross-tenant privilege grant.
    assert response.status_code == 404
