"""
scripts/mcp_spike.py — Step 0 walking-skeleton verification for the MCP restructure (B7).

Proves the full vertical slice over REAL MCP JSON-RPC:
    LLM (ChatGroq)  --create_react_agent-->  MCP client (tools/list)
                                                   |
                                                   v
                                       our FastMCP server  --ping-->  result
The LLM decides to call `ping` from its description; we never call it by hand.

Run it (two terminals, repo root, venv active):
    1)  python -m backend.mcp_server.server          # start the MCP server
    2)  python -m scripts.mcp_spike                   # run this client probe

Success looks like:
    - client logs "MCP tools/list → 1 tools: ['ping']"
    - server logs "tool ping(name=...) called"
    - final answer contains "pong: ... sdlc-mcp server is alive"
If that holds, the protocol/transport/LLM-tool-call machinery works and Step 1
(wrapping real Jira/GitHub connectors as tools) is just more @mcp.tool functions.
"""
import asyncio
import logging
import sys

from langgraph.prebuilt import create_react_agent

from backend.core.settings import settings
from backend.mcp_client.client import get_mcp_tools
from backend.providers.factory import LLMFactory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_spike")

# Default exercises a REAL tool (Step 1). Override from the CLI, e.g.:
#   python -m scripts.mcp_spike "what's the status of SDLC-5?"
#   python -m scripts.mcp_spike "which pull requests are open?"
_DEFAULT_QUERY = "What tickets are currently blocked in the SDLC project, and why?"

# A guiding system prompt is the single biggest fix for tool-use robustness:
# without it the model loops and leaks Llama's <|python_tag|> tool syntax into the
# final answer. Step 2's real node will carry a fuller version of this.
_SYSTEM = (
    "You are an SDLC assistant with live tools (Jira, GitHub, Slack, Confluence, "
    "Teams, Drive). To answer, call the most relevant tool(s) to fetch data, then "
    "write a clear, plain-English answer FROM the tool results. Rules: call each "
    "tool at most once per distinct need; never repeat an identical call; if a tool "
    "returns nothing, say so plainly. Your final message must be normal prose — "
    "never output tool-call syntax, function names, or <|python_tag|> markup."
)
# Cap the ReAct loop so a confused model can't spin (and burn Groq quota).
_RECURSION_LIMIT = 8


async def main() -> None:
    if settings.GROQ_API_KEY in ("", "placeholder"):
        raise SystemExit("GROQ_API_KEY is not set in .env — needed for the tool-use LLM.")

    query = " ".join(sys.argv[1:]).strip() or _DEFAULT_QUERY

    # 1) Discover tools from the MCP server (tools/list over JSON-RPC).
    tools = await get_mcp_tools()
    if not tools:
        raise SystemExit("No MCP tools discovered — is the server running on MCP_SERVER_URL?")

    # 2) Tool-calling model from the PROVIDER SEAM — not ChatGroq directly. Swap the
    #    provider in llm.yaml and this line is unchanged (provider-agnostic tool-use).
    model = LLMFactory.get_provider().get_chat_model()

    # 3) Standard LangGraph ReAct loop: bind tools + system prompt, let the LLM
    #    pick + call them, then synthesize a plain answer.
    agent = create_react_agent(model, tools, prompt=_SYSTEM)

    logger.info("Query: %s", query)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": query}]},
        {"recursion_limit": _RECURSION_LIMIT},
    )

    # Which MCP tools did the LLM actually call? (ToolMessages in the trajectory.)
    called = [m.name for m in result["messages"] if getattr(m, "type", "") == "tool"]
    final = result["messages"][-1].content

    print("\n================ MCP SPIKE RESULT ================")
    print(f"Query : {query}")
    print(f"Tools called by the LLM: {called or 'NONE'}")
    print("-------------------------------------------------")
    print(final)
    print("=================================================\n")
    if called:
        print(f"✅ PASS — LLM selected + called {len(called)} MCP tool(s) over JSON-RPC: {called}")
    else:
        print("⚠️  LLM answered without calling a tool — check tool descriptions / query.")


if __name__ == "__main__":
    asyncio.run(main())
