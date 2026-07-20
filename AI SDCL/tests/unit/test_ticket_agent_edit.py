"""
tests/unit/test_ticket_agent_edit.py
Unit tests for TicketAgent._run_edit_ticket() — specifically that a substring
replace ("X to Y") only touches the matched substring, whether or not the user
says "from". Omitting "from" used to silently fall through to the full-field
replace branch and wipe the rest of the description/title down to just the new
fragment. No Docker, no LLM, no MCP (jira_get_ticket is mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.ticket_agent import TicketAgent

CURRENT_DESCRIPTION = "v2.2 introduces two new additions. Changelog entry v2.2.0 added."


def _agent() -> TicketAgent:
    return TicketAgent.__new__(TicketAgent)  # skip __init__ — no llm/retriever needed for this path


async def _run(query: str, field_word: str = "description"):
    agent = _agent()
    state = {"query": query, "project_id": "SDLC"}
    ticket = {"title": "Update API versioning policy", "description": CURRENT_DESCRIPTION}
    with patch("backend.agents.ticket_agent.call_mcp_tool", new=AsyncMock(return_value=ticket)):
        return await agent._run_edit_ticket(state, "SDLC-7", field_word)


@pytest.mark.asyncio
async def test_from_to_replaces_only_the_substring():
    payload = await _run("Update SDLC-7 description from 2.2 to 2.3")
    assert payload.hitl_required is True
    new_value = payload.hitl_proposal["new_value"]
    assert new_value == "v2.3 introduces two new additions. Changelog entry v2.3.0 added."


@pytest.mark.asyncio
async def test_to_without_from_still_replaces_only_the_substring():
    """The exact bug: omitting "from" must NOT wipe the rest of the field."""
    payload = await _run("update SDLC-7 description 2.2 to 2.3")
    assert payload.hitl_required is True
    new_value = payload.hitl_proposal["new_value"]
    assert new_value == "v2.3 introduces two new additions. Changelog entry v2.3.0 added."
    assert "introduces two new additions" in new_value  # rest of the field preserved


@pytest.mark.asyncio
async def test_field_to_new_text_replaces_whole_field():
    payload = await _run("update SDLC-7 title to API versioning policy v3.0 rollout", field_word="title")
    assert payload.hitl_required is True
    assert payload.hitl_proposal["new_value"] == "API versioning policy v3.0 rollout"
    assert payload.hitl_proposal["field"] == "title"


@pytest.mark.asyncio
async def test_old_value_not_found_asks_for_clarification_without_hitl():
    payload = await _run("update SDLC-7 description from 9.9 to 10.0")
    assert payload.hitl_required is False
    assert "9.9" in payload.structured["final_response"]


@pytest.mark.asyncio
async def test_edit_intent_without_ticket_id_asks_which_ticket_not_create():
    """
    Fresh chat, no ticket ID anywhere ("that ticket" has no antecedent to resolve).
    Used to fall through past the edit-intent check straight into the ticket
    CREATE flow, silently proposing a nonsense new ticket instead of asking
    which ticket to edit.
    """
    agent = _agent()
    state = {"query": "Update description of that ticket to from '2.2' to '2.4'", "project_id": "SDLC"}
    payload = await agent.run(state)

    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "which ticket" in payload.structured["final_response"].lower()
