"""
Phase 18: the tenant layer of the hierarchical config resolver (§7), wired
to real storage and a real admin-facing PATCH endpoint for the first time.
Every resolve_config() call site previously passed platform-defaults alone —
these tests prove config_overrides actually flows all the way through to a
real request, not just round-trips through GET /v1/admin/providers.
"""
from __future__ import annotations

import json

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

async def test_updating_embedding_provider_is_reflected_in_provider_status(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        patch_response = await client.patch(
            "/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token)
        )
        get_response = await client.get("/v1/admin/providers", headers=_auth_header(token))

    assert patch_response.status_code == 200
    assert patch_response.json()["embedding_provider"] == "gemini"
    assert get_response.json()["embedding_provider"] == "gemini"


async def test_updating_only_one_field_leaves_the_other_at_its_default(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.patch(
            "/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token)
        )

    assert response.json()["llm_provider"] == "mock"  # untouched, still the platform default


async def test_overriding_llm_provider_actually_changes_which_provider_a_chat_turn_uses(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.patch("/v1/admin/config", json={"llm_provider": "groq"}, headers=_auth_header(token))

        # No GROQ_API_KEY is configured anywhere in this test environment
        # (by design — the whole suite runs with zero real API keys). If the
        # override reached the real request path, GroqProvider's constructor
        # raises ProviderMisconfiguredError immediately and chat_routes.py
        # turns that into a clean "error" SSE event. If the override were
        # silently ignored (the exact bug this feature fixes), this chat
        # turn would succeed normally with the mock provider instead.
        events = []
        async with client.stream(
            "POST", "/v1/chat", json={"content": "hello"}, headers=_auth_header(token)
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))

    assert events == [{"type": "error", "message": "chat.error"}]


# ── Negative ──────────────────────────────────────────────────────────────

async def test_updating_to_an_unknown_llm_provider_is_400(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.patch(
            "/v1/admin/config", json={"llm_provider": "not-a-real-provider"}, headers=_auth_header(token)
        )
    assert response.status_code == 400


async def test_updating_to_an_unknown_embedding_provider_is_400(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.patch(
            "/v1/admin/config", json={"embedding_provider": "not-a-real-provider"}, headers=_auth_header(token)
        )
    assert response.status_code == 400


async def test_a_rejected_update_never_touches_the_existing_override(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.patch("/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token))

        rejected = await client.patch(
            "/v1/admin/config", json={"embedding_provider": "not-a-real-provider"}, headers=_auth_header(token)
        )
        after = await client.get("/v1/admin/providers", headers=_auth_header(token))

    assert rejected.status_code == 400
    assert after.json()["embedding_provider"] == "gemini"  # the earlier valid override survives


async def test_update_config_without_users_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])  # read-only
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.patch(
            "/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token)
        )
    assert response.status_code == 403


async def test_update_config_requires_auth():
    async with await _client() as client:
        response = await client.patch("/v1/admin/config", json={"llm_provider": "mock"})
    assert response.status_code == 401


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_empty_update_body_leaves_config_unchanged(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.patch("/v1/admin/config", json={}, headers=_auth_header(token))

    assert response.status_code == 200
    assert response.json()["llm_provider"] == "mock"
    assert response.json()["embedding_provider"] == "mock"


async def test_setting_a_provider_back_to_a_known_value_works(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.patch("/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token))
        reverted = await client.patch(
            "/v1/admin/config", json={"embedding_provider": "mock"}, headers=_auth_header(token)
        )

    assert reverted.json()["embedding_provider"] == "mock"


# ── Side effects (tenant isolation) ──────────────────────────────────────

async def test_one_tenants_config_override_never_affects_another_tenant(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read", "users:write"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await client.patch("/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token_a))

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response_b = await client.get("/v1/admin/providers", headers=_auth_header(token_b))

    assert response_b.json()["embedding_provider"] == "mock"  # tenant B's own default, untouched
