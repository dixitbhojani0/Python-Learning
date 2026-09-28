"""
tests/unit/test_ticket_agent_history_resolution.py
Unit tests for TicketAgent's pronoun/ticket-ID resolution from session history (B10 gap).

Before this fix: "reassign that ticket to alice" / "log a note on it: ..." detected a
write verb (assign/comment) but no explicit ticket ID in the query, and — because the
old code required `ticket_id_match` directly — silently fell through every intent check
into the ticket CREATE flow, proposing a bogus new ticket. No Docker, no LLM (comment/ask
paths never call the LLM); assignment path mocks call_mcp_tool only.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.ticket_agent import TicketAgent


def _agent() -> TicketAgent:
    return TicketAgent.__new__(TicketAgent)  # skip __init__ — no llm/retriever needed for these paths


@pytest.mark.asyncio
async def test_comment_without_ticket_id_and_no_history_asks_not_create():
    agent = _agent()
    state = {"query": "log a note on it: investigated, this is a 3-day refactor", "project_id": "SDLC"}
    payload = await agent.run(state)

    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "which ticket" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_comment_without_ticket_id_resolves_from_recent_history():
    agent = _agent()
    state = {
        "query": "log a note on it: investigated, this is a 3-day refactor",
        "project_id": "SDLC",
        "recent_messages": [
            {"query": "what's the status of SDLC-9?",
             "response": "SDLC-9 is blocked, currently unassigned.",
             "role": "developer"},
        ],
    }
    payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "comment_ticket"
    assert payload.hitl_proposal["ticket_id"] == "SDLC-9"
    assert payload.hitl_proposal["comment"] == "investigated, this is a 3-day refactor"


@pytest.mark.asyncio
async def test_assign_without_ticket_id_and_no_history_asks_not_create():
    agent = _agent()
    state = {"query": "reassign that ticket to alice", "project_id": "SDLC"}
    payload = await agent.run(state)

    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "which ticket" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_assign_without_ticket_id_resolves_from_recent_history():
    agent = _agent()
    state = {
        "query": "reassign that to alice",
        "project_id": "SDLC",
        "recent_messages": [
            {"query": "what about SDLC-9?",
             "response": "SDLC-9 is blocked, currently unassigned.",
             "role": "developer"},
        ],
    }

    async def fake_call_mcp_tool(tool_name, _args):
        if tool_name == "jira_get_ticket":
            return {"title": "Fix login bug", "status": "IN_PROGRESS", "assignee": "unassigned"}
        if tool_name == "jira_get_project_members":
            return [{"display_name": "Alice", "name": "alice", "account_id": "acc-1", "active": True}]
        return {}

    with patch("backend.agents.ticket_agent.call_mcp_tool", new=AsyncMock(side_effect=fake_call_mcp_tool)):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "assign_ticket"
    assert payload.hitl_proposal["ticket_id"] == "SDLC-9"
    assert payload.hitl_proposal["assignee"] == "Alice"
