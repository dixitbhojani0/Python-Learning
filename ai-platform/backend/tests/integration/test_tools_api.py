"""
Phase 11 tool-calling + HITL tests: low-risk tools execute inline and their
result reaches the model (proven via the mock provider echoing its system
argument — the same trick used throughout this suite), high-risk tools stop
the turn and create a pending approval, and only an explicit approve/reject
by a permitted operator determines whether the tool ever actually runs.
"""
from __future__ import annotations

import json
import uuid

from httpx import ASGITransport, AsyncClient

from sqlalchemy import select

from backend.app.db.models import Conversation, PendingToolApproval, User
from backend.app.db.session import tenant_scoped_session
from backend.app.main import app
from backend.app.tools.base import BaseTool, ToolExecutionError
from backend.app.tools.registry import ToolRegistry

_TRANSPORT = ASGITransport(app=app)


async def _client() -> AsyncClient:
    return AsyncClient(transport=_TRANSPORT, base_url="http://testserver")


async def _login(client: AsyncClient, slug: str, email: str, password: str) -> str:
    response = await client.post("/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _send_chat(client: AsyncClient, token: str, content: str) -> list[dict]:
    events: list[dict] = []
    async with client.stream("POST", "/v1/chat", json={"content": content}, headers=_auth_header(token)) as response:
        assert response.status_code == 200, (await response.aread()).decode()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


async def _ingest(client: AsyncClient, token: str, title: str, content: str) -> None:
    response = await client.post("/v1/documents", json={"title": title, "content": content}, headers=_auth_header(token))
    assert response.status_code == 200, response.text


# ── Positive (low-risk tools execute inline) ─────────────────────────────

async def test_calculator_tool_executes_inline_and_result_reaches_the_model(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "calculate 21 + 21")

    tool_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool"] == "calculator"
    assert '"result": 42' in tool_events[0]["text"]

    token_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert '"result": 42' in token_text  # the mock provider echoed the tool result via `system`


async def test_current_time_tool_executes_inline(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "what time is it")

    tool_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool"] == "current_time"
    assert "utc_time" in tool_events[0]["text"]


async def test_normal_chat_without_a_tool_trigger_has_no_tool_events(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "hello there, how are you?")

    assert [e for e in events if e["type"] in ("tool_result", "approval_required")] == []


# ── Positive (high-risk tool: HITL pause) ────────────────────────────────

async def test_high_risk_tool_stops_the_turn_and_creates_a_pending_approval(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "delete all my documents")

    approval_events = [e for e in events if e["type"] == "approval_required"]
    assert len(approval_events) == 1
    assert approval_events[0]["tool"] == "delete_all_documents"
    # No token stream at all for a pending-approval turn — no LLM call was made.
    assert [e for e in events if e["type"] == "token"] == []


async def test_approving_a_pending_delete_actually_deletes_the_documents(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "tools:approve"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _ingest(client, token, "Doc A", "some content to be deleted")

        events = await _send_chat(client, token, "delete all my documents")
        approval_id = next(e for e in events if e["type"] == "approval_required")["approval_id"]

        approve_response = await client.post(f"/v1/admin/tool-approvals/{approval_id}/approve", headers=_auth_header(token))
        documents_after = await client.get("/v1/admin/documents", headers=_auth_header(token))

    assert approve_response.status_code == 200
    assert approve_response.json()["result"]["deleted_document_count"] == 1
    assert documents_after.json() == []


async def test_rejecting_a_pending_delete_leaves_the_documents_untouched(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "tools:approve"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _ingest(client, token, "Doc A", "must survive rejection")

        events = await _send_chat(client, token, "delete all my documents")
        approval_id = next(e for e in events if e["type"] == "approval_required")["approval_id"]

        reject_response = await client.post(f"/v1/admin/tool-approvals/{approval_id}/reject", headers=_auth_header(token))
        documents_after = await client.get("/v1/admin/documents", headers=_auth_header(token))

    assert reject_response.status_code == 200
    assert len(documents_after.json()) == 1  # untouched


async def test_pending_approval_appears_in_the_list_endpoint(make_tenant):
    tenant = await make_tenant(permissions=["tools:approve"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _send_chat(client, token, "delete all my documents")

        response = await client.get("/v1/admin/tool-approvals", headers=_auth_header(token))

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["tool_name"] == "delete_all_documents"
    assert response.json()[0]["status"] == "pending"


# ── Negative ──────────────────────────────────────────────────────────────

async def test_approve_requires_tools_approve_permission(make_tenant):
    tenant = await make_tenant(permissions=[])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "delete all my documents")
        approval_id = next(e for e in events if e["type"] == "approval_required")["approval_id"]

        response = await client.post(f"/v1/admin/tool-approvals/{approval_id}/approve", headers=_auth_header(token))

    assert response.status_code == 403


async def test_approving_a_nonexistent_approval_is_404(make_tenant):
    tenant = await make_tenant(permissions=["tools:approve"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/tool-approvals/00000000-0000-0000-0000-000000000000/approve", headers=_auth_header(token)
        )
    assert response.status_code == 404


async def test_approving_an_already_resolved_approval_is_409(make_tenant):
    tenant = await make_tenant(permissions=["tools:approve"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "delete all my documents")
        approval_id = next(e for e in events if e["type"] == "approval_required")["approval_id"]
        await client.post(f"/v1/admin/tool-approvals/{approval_id}/approve", headers=_auth_header(token))

        second_attempt = await client.post(f"/v1/admin/tool-approvals/{approval_id}/approve", headers=_auth_header(token))

    assert second_attempt.status_code == 409


async def test_calculator_with_invalid_expression_reports_a_tool_error_not_a_crash(make_tenant):
    tenant = await make_tenant()
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        events = await _send_chat(client, token, "calculate 1 / 0")

    tool_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_events) == 1
    assert "could not complete" in tool_events[0]["text"]
    # The turn still completes normally — a tool failure is not a request failure.
    assert any(e["type"] == "done" for e in events)


class _AlwaysFailsTool(BaseTool):
    """
    A high-risk tool that always fails on execute() — the only way to
    exercise approve_tool_call()'s "the human said yes, but the tool itself
    failed anyway" branch, since the platform's one real high-risk tool
    (delete_all_documents) has no natural failure mode to trigger honestly.
    """

    name = "always_fails"
    description = "test-only tool that always raises ToolExecutionError"
    risk_level = "high"

    async def execute(self, *, session, tenant_id, user_id, **kwargs):
        raise ToolExecutionError("simulated failure")


async def test_approving_a_tool_that_then_fails_still_marks_it_approved_with_the_error_recorded(make_tenant):
    tenant = await make_tenant(permissions=["tools:approve"])
    ToolRegistry.register("always_fails", _AlwaysFailsTool)

    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)

        # Bypassing chat/intent-detection deliberately — this tests
        # approve_tool_call()'s own failure handling directly, not whether a
        # message can trigger this specific (test-only) tool.
        approval_id = uuid.uuid4()
        async with tenant_scoped_session(tenant.tenant_id) as session:
            user_id = (await session.execute(select(User.id).where(User.email == tenant.admin_email))).scalar_one()

            conversation_id = uuid.uuid4()
            session.add(Conversation(id=conversation_id, tenant_id=tenant.tenant_id, user_id=user_id, title="x"))
            await session.flush()
            session.add(
                PendingToolApproval(
                    id=approval_id,
                    tenant_id=tenant.tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool_name="always_fails",
                    tool_args={},
                )
            )

        response = await client.post(f"/v1/admin/tool-approvals/{approval_id}/approve", headers=_auth_header(token))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"  # the human's decision, distinct from whether the tool itself succeeded
    assert "simulated failure" in body["result"]["error"]


# ── Edge / side effects (tenant isolation extends to approvals) ─────────

async def test_pending_approvals_never_cross_tenants(make_tenant):
    tenant_a = await make_tenant(permissions=["tools:approve"])
    tenant_b = await make_tenant(permissions=["tools:approve"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await _send_chat(client, token_a, "delete all my documents")

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response_b = await client.get("/v1/admin/tool-approvals", headers=_auth_header(token_b))

    assert response_b.json() == []
