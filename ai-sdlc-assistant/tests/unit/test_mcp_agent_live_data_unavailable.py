"""
tests/unit/test_mcp_agent_live_data_unavailable.py

B7c regression guard: when gather_via_tools() reports mcp_unavailable=True
(a live-data query that MCP couldn't serve), MCPAgent must return an honest
"live tools unavailable" message immediately — not silently fall through to
a RAG-only answer that could be stale (e.g. ticket status from an old sprint
doc) or hit the domain/low-confidence refusals, which say "I couldn't find
that" rather than "the live tools are down".

No Docker, no real LLM/MCP — retriever and gather_via_tools are mocked.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.mcp_agent import MCPAgent
from backend.mcp_client.tool_use import ToolCall, ToolGatherResult
from backend.rag.retriever import RetrievedChunk


class _FakeConfig:
    """Returns real strings/dicts so prompt assembly doesn't choke on MagicMock sentinels."""
    def get_prompt(self, key, **kwargs):
        return f"[{key}]"

    def get_temperature(self, key):
        return 0.4

    def get_llm_config(self):
        return {"primary": {"max_tokens": {"response": 500, "response_full_document": 900}}}


def _agent(chunks=None, confidence=0.9) -> MCPAgent:
    fake_retriever = MagicMock()
    fake_retriever.retrieve_with_corrective_rag = AsyncMock(
        return_value=(chunks or [], confidence, "first_pass")
    )
    return MCPAgent(mcp_registry=None, retriever=fake_retriever, llm=MagicMock(), config_loader=_FakeConfig())


@pytest.mark.asyncio
async def test_returns_honest_message_when_live_tools_unavailable():
    agent = _agent()
    state = {"query": "what's the status of SDLC-5?", "project_id": "SDLC", "user_role": "developer"}

    with patch(
        "backend.agents.mcp_agent.gather_via_tools",
        AsyncMock(return_value=ToolGatherResult(mcp_unavailable=True)),
    ):
        payload = await agent.run(state)

    assert "unavailable" in payload.structured["final_response"].lower()
    assert payload.structured["skip_persona"] is True
    assert payload.sources == []


@pytest.mark.asyncio
async def test_does_not_short_circuit_when_mcp_available():
    """Sanity check: a normal (mcp_unavailable=False) gather result must not trip the new guard."""
    chunk = RetrievedChunk(
        text="Design doc content", parent_text="Design doc content",
        source="doc:design.md", doc_type="doc", score=0.8, metadata={},
    )
    agent = _agent(chunks=[chunk])
    state = {"query": "what's the status of SDLC-5?", "project_id": "SDLC", "user_role": "developer"}

    async def _fake_generate(prompt, system, temperature, max_tokens):
        for tok in ["SDLC-5 is in progress."]:
            yield tok

    agent.llm.generate = _fake_generate

    # calls non-empty -> gathered.is_empty is False, so the (separate) faithfulness
    # gate — which only runs on pure-RAG answers — is skipped, same as live MCP data.
    gathered = ToolGatherResult(
        calls=[ToolCall(tool="jira_get_ticket", args={}, result={"status": "IN_PROGRESS"})],
        tools_called=["jira_get_ticket"],
    )
    with patch("backend.agents.mcp_agent.gather_via_tools", AsyncMock(return_value=gathered)):
        payload = await agent.run(state)

    assert "live tools" not in payload.structured["final_response"].lower()
