"""
tests/unit/test_mcp_agent_ticket_suggestion.py

B7 Step 4 regression guard: MCPAgent (the live generalist) never proposed
filing a Jira ticket for a problem described conversationally — that
behaviour existed only in the dead pre-MCP cross_source_agent. Ported onto
the real MCP path (jira_search_tickets for dedup, no keyword pre-filter —
the LLM prompt's own should_create rule is the correctness gate).

No Docker, no real LLM/MCP — retriever, llm, and call_mcp_tool are mocked.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.mcp_agent import MCPAgent
from backend.mcp_client.tool_use import ToolCall, ToolGatherResult


class _FakeConfig:
    def get_prompt(self, key, **kwargs):
        return f"[{key}]"

    def get_temperature(self, key):
        return 0.4

    def get_llm_config(self):
        return {"primary": {"max_tokens": {"response": 500, "response_full_document": 900}}}


def _agent(confidence=0.9) -> MCPAgent:
    fake_retriever = MagicMock()
    fake_retriever.retrieve_with_corrective_rag = AsyncMock(return_value=([], confidence, "first_pass"))
    return MCPAgent(mcp_registry=None, retriever=fake_retriever, llm=MagicMock(), config_loader=_FakeConfig())


async def _fake_generate(prompt, system, temperature, max_tokens):
    for tok in ["The checkout page returns 500s for guest users."]:
        yield tok


# Non-empty gathered result — makes gathered.is_empty False so the domain
# guard, low-confidence guard, and faithfulness gate all take the
# "live data came back" branch and let the real answer through unmodified,
# same as test_mcp_agent_live_data_unavailable.py's own pattern.
def _gathered_with_live_data() -> ToolGatherResult:
    return ToolGatherResult(
        calls=[ToolCall(tool="jira_get_blocked_tickets", args={}, result=[])],
        tools_called=["jira_get_blocked_tickets"],
    )


@pytest.mark.asyncio
async def test_proposes_ticket_for_untracked_problem():
    agent = _agent()
    agent.llm.generate = _fake_generate
    agent.llm.generate_structured = AsyncMock(return_value=MagicMock(
        parse_error=False,
        structured={"should_create": True, "title": "Checkout 500s for guests",
                    "description": "Guest checkout throws 500.", "priority": "HIGH", "labels": ["bug"]},
    ))
    state = {"query": "the checkout page is throwing 500s for guest users", "project_id": "SDLC", "user_role": "developer"}

    with patch("backend.agents.mcp_agent.gather_via_tools", AsyncMock(return_value=_gathered_with_live_data())), \
         patch("backend.mcp_client.client.call_mcp_tool", AsyncMock(return_value=[])):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "create_ticket"
    assert payload.hitl_proposal["title"] == "Checkout 500s for guests"
    assert "Untracked Issue Detected" in payload.structured["final_response"]


@pytest.mark.asyncio
async def test_no_proposal_when_issue_already_tracked():
    agent = _agent()
    agent.llm.generate = _fake_generate
    agent.llm.generate_structured = AsyncMock(return_value=MagicMock(
        parse_error=False,
        structured={"should_create": False, "title": "", "description": "", "priority": "MEDIUM", "labels": []},
    ))
    state = {"query": "the checkout page is throwing 500s for guest users", "project_id": "SDLC", "user_role": "developer"}

    with patch("backend.agents.mcp_agent.gather_via_tools", AsyncMock(return_value=_gathered_with_live_data())), \
         patch("backend.mcp_client.client.call_mcp_tool", AsyncMock(return_value=[{"id": "SDLC-9", "status": "OPEN", "title": "Checkout 500s"}])):
        payload = await agent.run(state)

    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "Untracked Issue Detected" not in payload.structured["final_response"]


@pytest.mark.asyncio
async def test_skips_check_entirely_for_historical_query():
    agent = _agent()
    agent.llm.generate = _fake_generate
    agent.llm.generate_structured = AsyncMock()
    state = {"query": "how was the checkout outage resolved last sprint?", "project_id": "SDLC", "user_role": "developer"}

    with patch("backend.agents.mcp_agent.gather_via_tools", AsyncMock(return_value=_gathered_with_live_data())), \
         patch("backend.mcp_client.client.call_mcp_tool", AsyncMock()) as mock_call:
        payload = await agent.run(state)

    agent.llm.generate_structured.assert_not_called()
    mock_call.assert_not_called()
    assert payload.hitl_required is False


@pytest.mark.asyncio
async def test_degrades_gracefully_when_similar_ticket_search_fails():
    agent = _agent()
    agent.llm.generate = _fake_generate
    agent.llm.generate_structured = AsyncMock(return_value=MagicMock(
        parse_error=False,
        structured={"should_create": True, "title": "X", "description": "Y", "priority": "LOW", "labels": []},
    ))
    state = {"query": "the checkout page is throwing 500s for guest users", "project_id": "SDLC", "user_role": "developer"}

    with patch("backend.agents.mcp_agent.gather_via_tools", AsyncMock(return_value=_gathered_with_live_data())), \
         patch("backend.mcp_client.client.call_mcp_tool", AsyncMock(side_effect=RuntimeError("MCP down"))):
        payload = await agent.run(state)

    assert payload.hitl_required is True
