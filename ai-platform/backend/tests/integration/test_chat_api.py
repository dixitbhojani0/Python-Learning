"""
Phase 3 chat tests — streaming response consumption, conversation
persistence, and the user-level isolation dimension RLS cannot cover on its
own (RLS only knows about tenants; two users in the SAME tenant must be
kept apart by application code, see api/chat_routes.py's explicit checks).
"""
from __future__ import annotations

import json
import uuid

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


async def _send_chat(client: AsyncClient, token: str, content: str, conversation_id: uuid.UUID | None = None):
    """Sends a chat message and consumes the SSE stream, returning (events, http_status)."""
    body = {"content": content}
    if conversation_id is not None:
        body["conversation_id"] = str(conversation_id)

    events: list[dict] = []
    async with client.stream("POST", "/v1/chat", json=body, headers=_auth_header(token)) as response:
        status_code = response.status_code
        if status_code == 200:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))
        else:
            await response.aread()
    return events, status_code, response


# ── Positive ──────────────────────────────────────────────────────────────

async def test_new_message_creates_conversation_and_streams_tokens(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events, status_code, _ = await _send_chat(client, token, "hello there")

        assert status_code == 200
        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]
        assert len(token_events) > 1  # genuinely multi-chunk, not one blob
        assert len(done_events) == 1
        conversation_id = done_events[0]["conversation_id"]

        history = await client.get(f"/v1/conversations/{conversation_id}/messages", headers=_auth_header(token))

    assert history.status_code == 200
    roles = [m["role"] for m in history.json()]
    assert roles == ["user", "assistant"]
    assert history.json()[0]["content"] == "hello there"
    assert "hello there" in history.json()[1]["content"]  # mock provider echoes the prompt


async def test_continuing_a_conversation_appends_to_the_same_thread(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events_1, _, _ = await _send_chat(client, token, "first message")
        conversation_id = events_1[-1]["conversation_id"]

        await _send_chat(client, token, "second message", conversation_id=uuid.UUID(conversation_id))

        history = await client.get(f"/v1/conversations/{conversation_id}/messages", headers=_auth_header(token))

    assert [m["content"] for m in history.json() if m["role"] == "user"] == ["first message", "second message"]


async def test_list_conversations_returns_only_the_callers_own(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _send_chat(client, token, "a conversation")

        response = await client.get("/v1/conversations", headers=_auth_header(token))

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "a conversation"


# ── Negative ──────────────────────────────────────────────────────────────

async def test_chat_without_auth_is_401():
    async with await _client() as client:
        response = await client.post("/v1/chat", json={"content": "hi"})
    assert response.status_code == 401


async def test_chat_empty_content_is_rejected(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post("/v1/chat", json={"content": ""}, headers=_auth_header(token))
    assert response.status_code == 422


async def test_continuing_a_nonexistent_conversation_is_404(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        _, status_code, _ = await _send_chat(client, token, "hi", conversation_id=uuid.uuid4())
    assert status_code == 404


# ── Edge (user-level isolation within one tenant — RLS doesn't cover this) ─

async def test_user_cannot_continue_another_users_conversation_in_same_tenant(make_tenant, add_user_to_tenant):
    tenant = await make_tenant()
    other_user = await add_user_to_tenant(tenant)

    async with await _client() as client:
        token_a = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events, _, _ = await _send_chat(client, token_a, "user A's private conversation")
        conversation_id = events[-1]["conversation_id"]

        token_b = await _login(client, tenant.slug, other_user.email, other_user.password)
        _, status_code, response = await _send_chat(
            client, token_b, "trying to hijack", conversation_id=uuid.UUID(conversation_id)
        )

    assert status_code == 404


async def test_user_cannot_read_another_users_conversation_history_in_same_tenant(make_tenant, add_user_to_tenant):
    tenant = await make_tenant()
    other_user = await add_user_to_tenant(tenant)

    async with await _client() as client:
        token_a = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events, _, _ = await _send_chat(client, token_a, "user A's private conversation")
        conversation_id = events[-1]["conversation_id"]

        token_b = await _login(client, tenant.slug, other_user.email, other_user.password)
        response = await client.get(f"/v1/conversations/{conversation_id}/messages", headers=_auth_header(token_b))

    assert response.status_code == 404
    assert "private" not in response.text


# ── Side effects ────────────────────────────────────────────────────────

async def test_list_conversations_does_not_leak_between_users_in_same_tenant(make_tenant, add_user_to_tenant):
    tenant = await make_tenant()
    other_user = await add_user_to_tenant(tenant)

    async with await _client() as client:
        token_a = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _send_chat(client, token_a, "A's conversation")

        token_b = await _login(client, tenant.slug, other_user.email, other_user.password)
        await _send_chat(client, token_b, "B's conversation")

        list_a = await client.get("/v1/conversations", headers=_auth_header(token_a))
        list_b = await client.get("/v1/conversations", headers=_auth_header(token_b))

    assert [c["title"] for c in list_a.json()] == ["A's conversation"]
    assert [c["title"] for c in list_b.json()] == ["B's conversation"]


# ── RAG-augmented chat (Phase 6: retrieval wired into /v1/chat) ──────────

async def test_chat_emits_citations_and_augmented_context_when_tenant_has_ingested_documents(make_tenant):
    tenant = await make_tenant()
    ingested_content = "the office wifi password is sunflower123"

    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        ingest_response = await client.post(
            "/v1/documents", json={"title": "Office facts", "content": ingested_content}, headers=_auth_header(token)
        )
        assert ingest_response.status_code == 200

        # Same text as the ingested chunk — guarantees a near-zero cosine
        # distance even with the non-semantic mock embedding (see
        # test_rag_api.py's docstring on why exact-text queries are used).
        events, status_code, _ = await _send_chat(client, token, ingested_content)

    assert status_code == 200
    citation_events = [e for e in events if e["type"] == "citations"]
    assert len(citation_events) == 1
    assert citation_events[0]["chunks"][0]["document_id"] == ingest_response.json()["id"]

    # The mock provider echoes its `system` argument (see mock_provider.py) —
    # this is the actual proof the retrieved content reached the LLM call,
    # not just that retrieval ran.
    token_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "sunflower123" in token_text


async def test_chat_has_no_citations_when_tenant_has_ingested_nothing(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events, status_code, _ = await _send_chat(client, token, "hello, is anyone there?")

    assert status_code == 200
    assert [e for e in events if e["type"] == "citations"] == []
    token_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "(system:" not in token_text  # no augmentation leaked into the reply when there's nothing to cite


async def test_chat_citations_never_include_another_tenants_documents(make_tenant):
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()
    shared_phrase = "the launch code is banana"

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await client.post(
            "/v1/documents", json={"title": "A's secret", "content": shared_phrase}, headers=_auth_header(token_a)
        )

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        events, status_code, _ = await _send_chat(client, token_b, shared_phrase)

    assert status_code == 200
    assert [e for e in events if e["type"] == "citations"] == []  # tenant B ingested nothing — RLS backstop holds here too
