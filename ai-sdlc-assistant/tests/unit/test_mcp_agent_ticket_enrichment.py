"""
tests/unit/test_mcp_agent_ticket_enrichment.py

E3/E4 regression guard: MCPAgent._enrich_ticket_refs formats the "Live Ticket
Statuses" section for tickets mentioned in RAG chunks — this used to drop
priority entirely (status/title/assignee/latest-comment only), so a
status-detail answer for a RAG-referenced ticket could never surface
priority even though the live Jira data already carries it. The latest
comment carries the real effort/ETA signal (B3/B4's "3-day refactor" case),
so it must keep showing up too.

No Docker, no real LLM — call_mcp_tool is mocked; _enrich_ticket_refs doesn't
touch self, so no retriever/llm/config setup is needed.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.mcp_agent import MCPAgent


@pytest.mark.asyncio
async def test_enrich_ticket_refs_includes_priority_and_latest_comment():
    agent = MCPAgent.__new__(MCPAgent)
    chunks = [SimpleNamespace(text="Sprint doc mentions SDLC-5 as in scope.")]
    fake_result = {
        "status": "IN_PROGRESS",
        "priority": "HIGH",
        "assignee": "Alice",
        "title": "Fix CORS bug",
        "comments": [
            {"author": "bob", "created": "2026-09-01", "body": "Investigated — this is actually a 3-day refactor"},
        ],
    }
    with patch("backend.mcp_client.client.call_mcp_tool", AsyncMock(return_value=fake_result)):
        section = await agent._enrich_ticket_refs(chunks)

    assert "SDLC-5" in section
    assert "[HIGH]" in section
    assert "Assignee: Alice" in section
    assert "3-day refactor" in section


@pytest.mark.asyncio
async def test_enrich_ticket_refs_defaults_priority_when_missing():
    agent = MCPAgent.__new__(MCPAgent)
    chunks = [SimpleNamespace(text="See SDLC-9 for details.")]
    fake_result = {"status": "TO_DO", "assignee": "unassigned", "title": "Flaky test", "comments": []}
    with patch("backend.mcp_client.client.call_mcp_tool", AsyncMock(return_value=fake_result)):
        section = await agent._enrich_ticket_refs(chunks)

    assert "[MEDIUM]" in section


@pytest.mark.asyncio
async def test_enrich_ticket_refs_returns_empty_when_no_ticket_ids():
    agent = MCPAgent.__new__(MCPAgent)
    chunks = [SimpleNamespace(text="No ticket mentioned here at all.")]
    section = await agent._enrich_ticket_refs(chunks)
    assert section == ""
