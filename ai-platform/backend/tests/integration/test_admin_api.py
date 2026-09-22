"""
Phase 9 admin control-plane tests: tenant/provider read endpoints, document
management (list/delete), RBAC gating per endpoint, and the same tenant-
isolation discipline (RLS backstop) as every other admin-ish route.
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


async def _ingest(client: AsyncClient, token: str, title: str, content: str) -> str:
    response = await client.post("/v1/documents", json={"title": title, "content": content}, headers=_auth_header(token))
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _send_chat(client: AsyncClient, token: str, content: str) -> None:
    """Sends a chat message and fully drains the stream (telemetry is recorded after the last token)."""
    async with client.stream("POST", "/v1/chat", json={"content": content}, headers=_auth_header(token)) as response:
        assert response.status_code == 200
        async for _ in response.aiter_lines():
            pass


# ── Positive ──────────────────────────────────────────────────────────────

async def test_get_tenant_returns_this_tenants_info(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/tenant", headers=_auth_header(token))

    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == tenant.slug
    assert body["id"] == str(tenant.tenant_id)


async def test_get_provider_status_reflects_the_registries(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/providers", headers=_auth_header(token))

    assert response.status_code == 200
    body = response.json()
    assert body["llm_provider"] == "mock"
    assert set(["mock", "gemini", "groq"]).issubset(body["available_llm_providers"])
    assert set(["mock", "gemini"]).issubset(body["available_embedding_providers"])


async def test_list_documents_reports_chunk_counts(make_tenant):
    tenant = await make_tenant(permissions=["documents:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        content = " ".join(f"w{i}" for i in range(25))  # 25 words -> 1 chunk (chunk_size=200)
        await _ingest(client, token, "Doc A", content)

        response = await client.get("/v1/admin/documents", headers=_auth_header(token))

    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1
    assert docs[0]["title"] == "Doc A"
    assert docs[0]["chunk_count"] == 1


async def test_delete_document_removes_it_and_its_chunks(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        doc_content = "the quarterly revenue figures for the coffee division"
        doc_id = await _ingest(client, token, "To delete", doc_content)

        delete_response = await client.delete(f"/v1/admin/documents/{doc_id}", headers=_auth_header(token))
        list_response = await client.get("/v1/admin/documents", headers=_auth_header(token))
        search_response = await client.get("/v1/search", params={"q": doc_content}, headers=_auth_header(token))

    assert delete_response.status_code == 200
    assert list_response.json() == []
    # Not just the Document row gone — its chunks must be gone too, or
    # search would still surface orphaned rows pointing at a dead document.
    assert search_response.json() == []


# ── Negative (auth + RBAC) ──────────────────────────────────────────────

async def test_admin_endpoints_require_auth():
    async with await _client() as client:
        assert (await client.get("/v1/admin/tenant")).status_code == 401
        assert (await client.get("/v1/admin/providers")).status_code == 401
        assert (await client.get("/v1/admin/documents")).status_code == 401
        assert (await client.delete("/v1/admin/documents/00000000-0000-0000-0000-000000000000")).status_code == 401


async def test_get_tenant_without_users_read_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=[])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/tenant", headers=_auth_header(token))
    assert response.status_code == 403


async def test_list_documents_without_documents_read_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])  # has users:read, not documents:read
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/documents", headers=_auth_header(token))
    assert response.status_code == 403


async def test_delete_document_without_documents_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["documents:read"])  # read-only
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.delete(
            "/v1/admin/documents/00000000-0000-0000-0000-000000000000", headers=_auth_header(token)
        )
    assert response.status_code == 403


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_delete_nonexistent_document_is_404(make_tenant):
    tenant = await make_tenant(permissions=["documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.delete(
            "/v1/admin/documents/00000000-0000-0000-0000-000000000000", headers=_auth_header(token)
        )
    assert response.status_code == 404


async def test_list_documents_empty_tenant_returns_empty_list(make_tenant):
    tenant = await make_tenant(permissions=["documents:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/documents", headers=_auth_header(token))
    assert response.json() == []


# ── Side effects (tenant isolation) ──────────────────────────────────────

async def test_list_documents_never_shows_another_tenants_documents(make_tenant):
    tenant_a = await make_tenant(permissions=["documents:read", "documents:write"])
    tenant_b = await make_tenant(permissions=["documents:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await _ingest(client, token_a, "A's doc", "some content here")

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.get("/v1/admin/documents", headers=_auth_header(token_b))

    assert response.json() == []


async def test_cannot_delete_another_tenants_document_by_id(make_tenant):
    tenant_a = await make_tenant(permissions=["documents:read", "documents:write"])
    tenant_b = await make_tenant(permissions=["documents:write"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        doc_id = await _ingest(client, token_a, "A's doc", "some content here")

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        delete_response = await client.delete(f"/v1/admin/documents/{doc_id}", headers=_auth_header(token_b))

        # A's document must still exist afterward.
        list_response = await client.get("/v1/admin/documents", headers=_auth_header(token_a))

    assert delete_response.status_code == 404  # RLS makes it invisible, not a cross-tenant delete
    assert len(list_response.json()) == 1


# ── Telemetry (Phase 10) ──────────────────────────────────────────────────

async def test_telemetry_summary_is_empty_before_any_chat_turn(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/telemetry", headers=_auth_header(token))

    assert response.status_code == 200
    assert response.json() == {
        "total_chat_turns": 0, "rag_usage_rate": 0.0, "memory_usage_rate": 0.0,
        "avg_latency_ms": 0.0, "avg_response_word_count": 0.0,
    }


async def test_telemetry_summary_reflects_rag_usage_after_a_chat_turn(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        doc_content = "the quarterly revenue figures for the coffee division"
        await _ingest(client, token, "Doc", doc_content)
        await _send_chat(client, token, doc_content)  # exact-match query -> retrieved as RAG context

        response = await client.get("/v1/admin/telemetry", headers=_auth_header(token))

    body = response.json()
    assert body["total_chat_turns"] == 1
    assert body["rag_usage_rate"] == 1.0
    assert body["avg_response_word_count"] > 0
    assert body["avg_latency_ms"] >= 0.0


async def test_telemetry_summary_counts_multiple_turns_correctly(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _send_chat(client, token, "first message, no corpus ingested")
        await _send_chat(client, token, "second message, still no corpus ingested")

        response = await client.get("/v1/admin/telemetry", headers=_auth_header(token))

    body = response.json()
    assert body["total_chat_turns"] == 2
    assert body["rag_usage_rate"] == 0.0  # nothing was ever ingested in this tenant


async def test_telemetry_endpoint_requires_users_read_permission(make_tenant):
    tenant = await make_tenant(permissions=[])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/telemetry", headers=_auth_header(token))
    assert response.status_code == 403


async def test_telemetry_summary_never_mixes_tenants(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await _send_chat(client, token_a, "tenant A's message")

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response_b = await client.get("/v1/admin/telemetry", headers=_auth_header(token_b))

    assert response_b.json()["total_chat_turns"] == 0  # A's chat turn must not show up in B's summary
