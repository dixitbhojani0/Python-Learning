"""
backend/mcp_client — host-side MCP client.

The single boundary through which the FastAPI/LangGraph host consumes MCP
servers. It connects to one or more MCP servers, runs `tools/list`, and hands
back LangChain-compatible tools for the LLM to choose and call.

Host/agent code depends only on `get_mcp_tools()` — it never knows the transport,
URL, or which server a tool came from. That keeps the host decoupled from the
MCP server and lets us add/point at more servers (our own, or official
Atlassian/GitHub servers) without touching agent code.
"""
