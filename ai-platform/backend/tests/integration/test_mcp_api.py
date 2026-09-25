"""
Phase 21: connect/discover/manually-test external MCP servers
(api/mcp_routes.py). test-connection and call-tool talk to a REAL MCP
server, not a mock — the same `mcp_test_server` fixture
backend/tests/unit/test_mcp_client.py uses (see its docstring, and
tests/unit/_mcp_test_server.py's, for why that server runs as a genuine
separate OS process).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.tests.unit._mcp_test_server import PORT as _MCP_PORT
from backend.tests.unit._mcp_test_server import TOKEN as _MCP_TOKEN

pytestmark = pytest.mark.usefixtures("mcp_test_server")

_TRANSPORT = ASGITransport(app=app)
_MCP_URL = f"http://127.0.0.1:{_MCP_PORT}/mcp"


async def _client() -> AsyncClient:
    return AsyncClient(transport=_TRANSPORT, base_url="http://testserver")


async def _login(client: AsyncClient, slug: str, email: str, password: str) -> str:
    response = await client.post("/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_server(client: AsyncClient, token: str, *, name: str = "Test MCP", auth_token: str | None = _MCP_TOKEN) -> dict:
    response = await client.post(
        "/v1/admin/mcp-servers",
        json={"name": name, "url": _MCP_URL, "auth_token": auth_token},
        headers=_auth_header(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


# ── Positive ──────────────────────────────────────────────────────────────

async def test_creating_a_server_returns_it_without_the_auth_token(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        body = await _create_server(client, token)

    assert body["name"] == "Test MCP"
    assert body["url"] == _MCP_URL
    assert body["enabled"] is True
    assert "auth_token" not in body


async def test_listing_servers_returns_created_ones(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await _create_server(client, token)
        response = await client.get("/v1/admin/mcp-servers", headers=_auth_header(token))

    assert response.status_code == 200
    servers = response.json()
    assert len(servers) == 1
    assert servers[0]["name"] == "Test MCP"


async def test_connection_test_discovers_the_real_servers_tools(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        server = await _create_server(client, token)
        response = await client.post(f"/v1/admin/mcp-servers/{server['id']}/test-connection", headers=_auth_header(token))

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert names == {"add", "boom"}


async def test_call_tool_invokes_the_real_tool(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        server = await _create_server(client, token)
        response = await client.post(
            f"/v1/admin/mcp-servers/{server['id']}/call-tool",
            json={"tool_name": "add", "arguments": {"a": 4, "b": 5}},
            headers=_auth_header(token),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["is_error"] is False
    assert body["content"][0]["text"] == "9"


async def test_deleting_a_server_removes_it_from_the_list(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        server = await _create_server(client, token)
        delete_response = await client.delete(f"/v1/admin/mcp-servers/{server['id']}", headers=_auth_header(token))
        list_response = await client.get("/v1/admin/mcp-servers", headers=_auth_header(token))

    assert delete_response.status_code == 200
    assert list_response.json() == []


# ── Negative ──────────────────────────────────────────────────────────────

async def test_create_without_users_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/mcp-servers", json={"name": "x", "url": _MCP_URL}, headers=_auth_header(token)
        )
    assert response.status_code == 403


async def test_list_without_users_read_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/mcp-servers", headers=_auth_header(token))
    assert response.status_code == 403


async def test_delete_without_users_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        server = await _create_server(client, token)

    tenant_b = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.delete(f"/v1/admin/mcp-servers/{server['id']}", headers=_auth_header(token_b))
    assert response.status_code == 403


async def test_test_connection_for_unknown_server_is_404(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/mcp-servers/00000000-0000-0000-0000-000000000000/test-connection", headers=_auth_header(token)
        )
    assert response.status_code == 404


async def test_call_tool_for_unknown_server_is_404(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/mcp-servers/00000000-0000-0000-0000-000000000000/call-tool",
            json={"tool_name": "add", "arguments": {}},
            headers=_auth_header(token),
        )
    assert response.status_code == 404


async def test_connection_test_with_a_wrong_stored_token_is_a_502(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        server = await _create_server(client, token, auth_token="not-the-real-token")
        response = await client.post(f"/v1/admin/mcp-servers/{server['id']}/test-connection", headers=_auth_header(token))
    assert response.status_code == 502


async def test_call_tool_on_a_disabled_server_is_400(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/mcp-servers",
            json={"name": "Disabled", "url": _MCP_URL, "auth_token": _MCP_TOKEN, "enabled": False},
            headers=_auth_header(token),
        )
        server_id = create.json()["id"]
        response = await client.post(
            f"/v1/admin/mcp-servers/{server_id}/call-tool",
            json={"tool_name": "add", "arguments": {"a": 1, "b": 1}},
            headers=_auth_header(token),
        )
    assert response.status_code == 400


async def test_create_and_list_and_delete_require_auth(make_tenant):
    async with await _client() as client:
        create = await client.post("/v1/admin/mcp-servers", json={"name": "x", "url": _MCP_URL})
        listing = await client.get("/v1/admin/mcp-servers")
    assert create.status_code == 401
    assert listing.status_code == 401


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_listing_with_no_servers_returns_an_empty_list(make_tenant):
    tenant = await make_tenant(permissions=["users:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/mcp-servers", headers=_auth_header(token))
    assert response.json() == []


async def test_creating_without_an_auth_token_defaults_it_to_none(make_tenant):
    tenant = await make_tenant(permissions=["users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/mcp-servers", json={"name": "No token", "url": _MCP_URL}, headers=_auth_header(token)
        )
    assert response.status_code == 201
    assert response.json()["enabled"] is True


# ── Side effects (tenant isolation) ──────────────────────────────────────

async def test_a_tenants_servers_are_invisible_to_another_tenant(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read", "users:write"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await _create_server(client, token_a)

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.get("/v1/admin/mcp-servers", headers=_auth_header(token_b))

    assert response.json() == []


async def test_cannot_test_connection_on_another_tenants_server_by_id(make_tenant):
    tenant_a = await make_tenant(permissions=["users:read", "users:write"])
    tenant_b = await make_tenant(permissions=["users:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        server = await _create_server(client, token_a)

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.post(f"/v1/admin/mcp-servers/{server['id']}/test-connection", headers=_auth_header(token_b))

    assert response.status_code == 404
