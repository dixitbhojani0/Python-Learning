"""
scripts/mcp_gather_probe.py — Step 2 verification for the gather-then-synthesize loop.

Proves backend/mcp_client/tool_use.gather_via_tools: the LLM selects + calls MCP
tools and we collect the LIVE DATA + trace (no final answer here — synthesis is
the app's job in Step 3).

Run (MCP server must be up in another terminal):
    python -m backend.mcp_server.server          # terminal 1
    python -m scripts.mcp_gather_probe "what's blocking the SDLC sprint?"   # terminal 2

Success = it prints the tools the LLM chose, then the formatted live-data context
that would be fed to our generation pipeline.
"""
import asyncio
import logging
import sys

from backend.core.settings import settings
from backend.mcp_client.tool_use import gather_via_tools

logging.basicConfig(level=logging.INFO)

_DEFAULT_QUERY = "What is blocking the SDLC sprint, and are there any open PRs adding risk?"


async def main() -> None:
    if settings.GROQ_API_KEY in ("", "placeholder"):
        raise SystemExit("GROQ_API_KEY is not set in .env — needed for the tool-use LLM.")

    query = " ".join(sys.argv[1:]).strip() or _DEFAULT_QUERY
    result = await gather_via_tools(query)

    print("\n============ MCP GATHER PROBE ============")
    print(f"Query        : {query}")
    print(f"Tools called : {result.tools_called or 'NONE'}")
    print("----------- live-data context ------------")
    print(result.as_context() or "(empty — RAG-only fallback)")
    print("==========================================\n")
    if result.calls:
        print(f"✅ Step 2 PASS — gathered {len(result.calls)} tool result(s); ready for synthesis.")
    else:
        print("⚠️  No tool data gathered — check the server / query.")


if __name__ == "__main__":
    asyncio.run(main())
