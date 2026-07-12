"""
scripts/mcp_agent_probe.py — Step 3 verification for MCPAgent (the new default agent).

Runs the full gather-then-synthesize agent in isolation (no API/auth/frontend):
    RAG retrieve  +  MCP tool-use gather  ->  synthesized answer (AgentPayload).

Prereqs (other terminals): Qdrant running + data ingested (for RAG), and the SDLC
MCP server up:
    python -m backend.mcp_server.server

Run:
    python -m scripts.mcp_agent_probe "What is blocking the dashboard, and any risky PRs?"
    python -m scripts.mcp_agent_probe "what's the status of SDLC-5?"
"""
import asyncio
import logging
import sys

from backend.core.config_loader import config
from backend.core.settings import settings
from backend.mcp.registry import MCPRegistry
from backend.agents.mcp_agent import MCPAgent
from backend.providers.factory import LLMFactory
from backend.rag.retriever import HybridRetriever

logging.basicConfig(level=logging.INFO)

_DEFAULT_QUERY = "What is blocking the dashboard feature, and are there any risky open PRs?"
_ROLE = "manager"


async def main() -> None:
    if settings.GROQ_API_KEY in ("", "placeholder"):
        raise SystemExit("GROQ_API_KEY is not set in .env.")

    query = " ".join(sys.argv[1:]).strip() or _DEFAULT_QUERY
    agent = MCPAgent(
        retriever=HybridRetriever(),
        llm=LLMFactory.get_provider(),
        config_loader=config,
        mcp_registry=MCPRegistry(),
    )
    state = {
        "query": query,
        "project_id": settings.DEFAULT_PROJECT,
        "user_role": _ROLE,
        "recent_messages": [],
    }
    payload = await agent.run(state)
    s = payload.structured

    print("\n============== MCP AGENT PROBE (Step 3) ==============")
    print(f"Query        : {query}  (role={_ROLE})")
    print(f"RAG strategy : {s.get('rag_strategy')}  confidence={payload.confidence:.3f}  "
          f"chunks={len(s.get('rag_chunks', []))}")
    print(f"MCP calls    : {[c['tool'] for c in s.get('mcp_calls', [])] or 'NONE'}")
    print(f"Sources      : {payload.sources}")
    print("------------------- answer --------------------------")
    print(s.get("final_response", "(empty)"))
    print("=====================================================\n")
    if s.get("mcp_calls") or s.get("rag_chunks"):
        print("✅ Step 3 PASS — agent gathered via MCP + RAG and synthesized an answer.")
    else:
        print("⚠️  No MCP or RAG data — check the MCP server / Qdrant / query.")


if __name__ == "__main__":
    asyncio.run(main())
