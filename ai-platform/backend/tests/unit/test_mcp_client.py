"""
backend/app/mcp/client.py against a REAL MCP server, not mocks — the same
"verify empirically" discipline this codebase has followed all along (see
e.g. test_document_parsers.py's real malformed-file tests). The server
(_mcp_test_server.py, started by the shared `mcp_test_server` fixture in
tests/conftest.py) runs as a genuine separate OS process — see that
module's own docstring for why it isn't hosted in-process inside this
test's event loop instead (a real, reproducible hang under pytest-asyncio's
session loop that a standalone process sidesteps).
"""
from __future__ import annotations

import pytest

from backend.app.mcp.client import McpConnectionError, call_tool, discover_tools
from backend.tests.unit._mcp_test_server import PORT, TOKEN

pytestmark = pytest.mark.usefixtures("mcp_test_server")

_URL = f"http://127.0.0.1:{PORT}/mcp"


# ── Positive ──────────────────────────────────────────────────────────────

async def test_discover_tools_lists_the_servers_real_tools():
    tools = await discover_tools(_URL, auth_token=TOKEN)
    names = {tool["name"] for tool in tools}
    assert names == {"add", "boom"}
    add_tool = next(t for t in tools if t["name"] == "add")
    assert add_tool["input_schema"]["required"] == ["a", "b"]


async def test_call_tool_invokes_the_real_tool_and_returns_its_result():
    result = await call_tool(_URL, "add", {"a": 2, "b": 3}, auth_token=TOKEN)
    assert result["is_error"] is False
    assert result["content"][0]["text"] == "5"


# ── Negative ──────────────────────────────────────────────────────────────

async def test_discover_tools_without_a_token_raises_connection_error():
    with pytest.raises(McpConnectionError):
        await discover_tools(_URL)


async def test_discover_tools_with_the_wrong_token_raises_connection_error():
    with pytest.raises(McpConnectionError):
        await discover_tools(_URL, auth_token="wrong-token")


async def test_discover_tools_against_an_unreachable_server_raises_connection_error():
    # Confirmed empirically (not assumed) that a bad URL surfaces as a bare
    # ExceptionGroup from the SDK's internal TaskGroup, not a clean
    # ConnectionError — this is exactly what the wrapper's broad `except
    # Exception` exists to normalize into one error type callers can handle.
    with pytest.raises(McpConnectionError):
        await discover_tools("http://127.0.0.1:1/mcp")


async def test_call_tool_against_an_unreachable_server_raises_connection_error():
    with pytest.raises(McpConnectionError):
        await call_tool("http://127.0.0.1:1/mcp", "add", {"a": 1, "b": 1})


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_call_tool_where_the_tool_itself_raises_is_a_protocol_error_not_a_connection_error():
    # The connection and handshake succeeded; the tool's own code failed.
    # That's a real, distinct failure mode the MCP protocol itself
    # represents as CallToolResult.is_error=True, not a raised exception —
    # the wrapper must not conflate the two.
    result = await call_tool(_URL, "boom", {}, auth_token=TOKEN)
    assert result["is_error"] is True
