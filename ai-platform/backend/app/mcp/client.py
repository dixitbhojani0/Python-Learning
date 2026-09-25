"""
backend/app/mcp/client.py

Thin wrapper around the official `mcp` SDK's v2 `Client` (see
requirements.txt's comment for why that SDK, not `langchain-mcp-adapters`)
for the one thing Phase 21 actually needs: connect to an admin-configured
MCP server, list its tools, and invoke one manually for testing. This is
deliberately NOT wired into the chat/agent tool-execution loop (see
api/mcp_routes.py's module docstring for the honest reason why not) — every
function here is only ever called from an explicit admin action.

Connecting to an unreachable or misbehaving server does not raise a clean
exception type — confirmed empirically, not assumed (see
tests/unit/test_mcp_client.py): a bad URL surfaces as a bare
`ExceptionGroup` from the SDK's internal `anyio` TaskGroup, not a
`ConnectionError` or the SDK's own `MCPError`. So both entry points here
catch broadly and re-raise as the one error type callers actually need to
handle.

Optional bearer-token auth: the SDK's simple `Client(url_string)` path has
no way to attach headers, but passing a `streamable_http_client(url,
http_client=...)` transport (a lower-level call the SDK's own `Client` makes
internally for the url-string case — confirmed by reading its source, not
guessed) accepts a custom `httpx2.AsyncClient`, which does.
"""
from __future__ import annotations

from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

_READ_TIMEOUT_SECONDS = 15


class McpConnectionError(Exception):
    """A connection, handshake, or protocol-level failure talking to an MCP server."""


def _transport(url: str, auth_token: str | None) -> str | Any:
    if not auth_token:
        return url
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {auth_token}"})
    return streamable_http_client(url, http_client=http_client)


async def discover_tools(url: str, auth_token: str | None = None) -> list[dict[str, Any]]:
    """Connect, list the server's tools, and disconnect. Read-only from the MCP server's perspective."""
    try:
        async with Client(_transport(url, auth_token), read_timeout_seconds=_READ_TIMEOUT_SECONDS) as client:
            result = await client.list_tools()
    except Exception as exc:
        raise McpConnectionError(str(exc)) from exc
    return [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in result.tools]


async def call_tool(url: str, tool_name: str, arguments: dict[str, Any], auth_token: str | None = None) -> dict[str, Any]:
    """Connect, invoke one tool, and disconnect. May have side effects on the external server — a manual admin action, not autonomous chat-triggered use."""
    try:
        async with Client(_transport(url, auth_token), read_timeout_seconds=_READ_TIMEOUT_SECONDS) as client:
            result = await client.call_tool(tool_name, arguments)
    except Exception as exc:
        raise McpConnectionError(str(exc)) from exc
    return {"is_error": result.is_error, "content": [block.model_dump(mode="json") for block in result.content]}
