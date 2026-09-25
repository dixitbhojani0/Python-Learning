"""
Standalone MCP test server, run as its own OS process by test_mcp_client.py
(not hosted in-process inside the pytest event loop).

# ponytail: this exists because hosting the same real uvicorn server
in-process inside pytest-asyncio's session-scoped loop was tried first and
hit a real, reproducible hang — authenticated requests (the ones that
actually reach the MCP SDK's streamable_http session manager, not just this
module's own auth-rejecting middleware) never completed, timing out in
httpx2, while the identical server code ran correctly as a standalone
`asyncio.run()` process. Root cause not fully chased down (looked like an
anyio task-group/loop interaction specific to pytest-asyncio's shared
session loop) — running the server as a real separate process sidesteps it
entirely and is also a more faithful "real MCP server" test in the first
place. Upgrade path if this ever needs to change: revisit once `mcp` ships a
newer release, or once this hang is understood.
"""
from __future__ import annotations

import uvicorn
from mcp.server.mcpserver import MCPServer

PORT = 18541
TOKEN = "test-bearer-token"

server = MCPServer("guarded-test-server")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@server.tool()
def boom() -> str:
    """A tool that always fails, to exercise a protocol-level tool error."""
    raise ValueError("boom")


class _RequireBearerToken:
    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if headers.get(b"authorization") != f"Bearer {TOKEN}".encode():
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


app = _RequireBearerToken(server.streamable_http_app())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
