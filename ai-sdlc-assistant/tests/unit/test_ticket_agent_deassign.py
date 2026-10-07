"""
tests/unit/test_ticket_agent_deassign.py
Unit tests for TicketAgent's deassign intent (E7). "deassign"/"unassign" don't match
\\bassign\\b (no word boundary before the embedded "assign"), so these queries used to fall
through every intent branch into the ticket CREATE flow instead of clearing the assignee —
same bug class B10 fixed for assign/edit/comment. No Docker, no LLM, no MCP (call_mcp_tool
is mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.ticket_agent import TicketAgent


def _agent() -> TicketAgent:
    return TicketAgent.__new__(TicketAgent)  # skip __init__ — no llm/retriever needed for this path


@pytest.mark.asyncio
async def test_deassign_with_assignee_proposes_hitl():
    ticket = {"title": "Fix checkout 500s", "assignee": "Alice"}
    agent = _agent()
    state = {"query": "deassign SDLC-5", "project_id": "SDLC", "recent_messages": []}
    with patch("backend.agents.ticket_agent.call_mcp_tool", new=AsyncMock(return_value=ticket)):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "deassign_ticket"
    assert payload.hitl_proposal["ticket_id"] == "SDLC-5"
    assert payload.hitl_proposal["assignee"] == "Alice"


@pytest.mark.asyncio
async def test_unassign_verb_also_matches():
    ticket = {"title": "Fix checkout 500s", "assignee": "Bob"}
    agent = _agent()
    state = {"query": "unassign SDLC-5", "project_id": "SDLC", "recent_messages": []}
    with patch("backend.agents.ticket_agent.call_mcp_tool", new=AsyncMock(return_value=ticket)):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "deassign_ticket"


@pytest.mark.asyncio
async def test_already_unassigned_is_a_noop_no_hitl():
    ticket = {"title": "Fix checkout 500s", "assignee": "unassigned"}
    agent = _agent()
    state = {"query": "deassign SDLC-5", "project_id": "SDLC", "recent_messages": []}
    with patch("backend.agents.ticket_agent.call_mcp_tool", new=AsyncMock(return_value=ticket)):
        payload = await agent.run(state)

    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "already unassigned" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_deassign_without_ticket_id_asks_which_ticket_not_create():
    """
    The exact bug: "deassign that ticket" with no antecedent in history used to fall
    through past every intent check into the ticket CREATE flow.
    """
    agent = _agent()
    state = {"query": "deassign that ticket", "project_id": "SDLC", "recent_messages": []}
    payload = await agent.run(state)

    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "which ticket" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_deassign_resolves_ticket_id_from_history():
    """"deassign it" with no explicit ID — resolves against the last ticket mentioned
    in session history, same rule as assign/edit/comment (B10)."""
    ticket = {"title": "Fix checkout 500s", "assignee": "Alice"}
    agent = _agent()
    state = {
        "query": "deassign it",
        "project_id": "SDLC",
        "recent_messages": [{"query": "what's the status of SDLC-9?", "response": "SDLC-9 is in progress."}],
    }
    with patch("backend.agents.ticket_agent.call_mcp_tool", new=AsyncMock(return_value=ticket)):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["ticket_id"] == "SDLC-9"
