"""
backend/mcp_server/tools — MCP tool modules, grouped by domain.

Each module exposes:
    register(mcp, registry) -> None
which adds that domain's @mcp.tool functions to the FastMCP server. The tool
bodies are thin wrappers that delegate to the existing connectors in
`backend.mcp.connectors` (via the shared MCPRegistry) — so the integration logic
(REST calls, auth, mock fallback) is reused unchanged, and the only thing this
layer adds is the MCP tool surface + the LLM-facing tool descriptions (docstrings).

Add a new connector's tools = add one module here + one register() line in
server.py. Nothing else in the system changes (the client auto-discovers via
tools/list; the LLM reads the new descriptions).
"""
