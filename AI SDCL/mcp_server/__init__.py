"""
backend/mcp_server — our own MCP (Model Context Protocol) server.

This package exposes the assistant's capabilities (Jira, GitHub, Slack, …) as
real MCP *tools* over JSON-RPC, so the LLM — not hand-written agent code —
discovers and calls them (`tools/list` → LLM picks → `tools/call`).

It runs as a **separate, decoupled service** (streamable-HTTP transport), so it
can be scaled, deployed, and restarted independently of the FastAPI host. The
host consumes it through `backend/mcp_client`.

Structure (grows in Step 1):
    server.py            FastMCP app + transport entrypoint
    tools/jira_tools.py  @mcp.tool wrappers → backend.mcp.connectors (the impl)
    tools/github_tools.py
    ...
Each tool module will expose `register(mcp)` so the server composes them without
this package knowing connector internals (decoupling).
"""
