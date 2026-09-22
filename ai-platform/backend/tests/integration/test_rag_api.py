"""
Phase 5 RAG tests: ingestion produces the expected chunk count, search finds
the right chunk, and the same tenant/user isolation discipline as chat/auth
extends to documents and chunks.

Search-relevance assertions deliberately query with the EXACT text of an
ingested chunk, not a paraphrase — the mock embedding provider is hash-based,
not semantic (see its docstring), so "finds an exact match" is what it can
honestly prove; "finds a related-but-differently-worded chunk" would need a
real embedding provider and is out of scope for a network-free test suite.
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

async def test_ingest_reports_the_expected_chunk_count(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        # 25 words, chunk_size=200 by default -> exactly one chunk.
        content = " ".join(f"word{i}" for i in range(25))

        response = await client.post(
            "/v1/documents", json={"title": "Doc A", "content": content}, headers=_auth_header(token)
        )

    assert response.status_code == 200
    assert response.json()["chunk_count"] == 1


async def test_search_finds_the_exact_chunk_as_top_result(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        target_content = "the quarterly revenue report for the coffee division"
        await client.post(
            "/v1/documents", json={"title": "Target", "content": target_content}, headers=_auth_header(token)
        )
        await client.post(
            "/v1/documents",
            json={"title": "Unrelated", "content": "completely different topic about astronomy"},
            headers=_auth_header(token),
        )

        response = await client.get(
            "/v1/search", params={"q": target_content, "top_k": 5}, headers=_auth_header(token)
        )

    assert response.status_code == 200
    results = response.json()
    assert results[0]["content"] == target_content
    assert results[0]["distance"] < 1e-6
    # Ordered by increasing distance — the unrelated chunk must not rank first.
    assert results[0]["distance"] <= results[-1]["distance"]


# ── Negative ──────────────────────────────────────────────────────────────

async def test_ingest_without_auth_is_401():
    async with await _client() as client:
        response = await client.post("/v1/documents", json={"title": "x", "content": "y"})
    assert response.status_code == 401


async def test_search_without_auth_is_401():
    async with await _client() as client:
        response = await client.get("/v1/search", params={"q": "anything"})
    assert response.status_code == 401


async def test_ingest_empty_content_is_rejected(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/documents", json={"title": "x", "content": ""}, headers=_auth_header(token)
        )
    assert response.status_code == 422


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_search_with_no_ingested_documents_returns_empty_list_not_error(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/search", params={"q": "anything"}, headers=_auth_header(token))

    assert response.status_code == 200
    assert response.json() == []


# ── Side effects (tenant isolation extends to documents/chunks) ─────────

async def test_search_never_returns_another_tenants_chunks(make_tenant):
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()
    shared_phrase = "quarterly revenue report"

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await client.post(
            "/v1/documents", json={"title": "A's doc", "content": shared_phrase}, headers=_auth_header(token_a)
        )

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.get(
            "/v1/search", params={"q": shared_phrase, "top_k": 5}, headers=_auth_header(token_b)
        )

    assert response.status_code == 200
    assert response.json() == []  # tenant B has ingested nothing — A's chunk must not leak across
