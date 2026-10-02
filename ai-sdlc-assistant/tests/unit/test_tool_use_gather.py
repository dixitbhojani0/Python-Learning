"""
tests/unit/test_tool_use_gather.py

Unit tests for the B7c "live data needed but unavailable" signal on
gather_via_tools() / ToolGatherResult. Before this fix, MCP being down or a
tool call erroring out looked identical to "this query never needed live
data" (both landed on ToolGatherResult() / is_empty=True) — MCPAgent would
silently degrade to a possibly-stale RAG-only answer for a live-data query
(ticket status, PR state) instead of saying live tools are unavailable.

No Docker, no real MCP server, no real LLM — the MCP client (get_mcp_tools /
ainvoke_tool) and the LLM provider are mocked.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from backend.mcp_client import tool_use


def _fake_tool(name: str):
    return SimpleNamespace(name=name)


class _FakeStructuredResponse:
    def __init__(self, structured=None, is_empty=False, parse_error=False):
        self.structured = structured or {}
        self.is_empty = is_empty
        self.parse_error = parse_error


@pytest.mark.asyncio
async def test_mcp_down_and_query_needs_live_data_marks_unavailable():
    fake_provider = MagicMock()
    fake_provider.generate_structured = AsyncMock(return_value=_FakeStructuredResponse(
        structured={"needs_live_data": True, "reason": "asks for a ticket's current status"}
    ))
    with patch.object(tool_use, "get_mcp_tools", AsyncMock(side_effect=RuntimeError("connection refused"))), \
         patch.object(tool_use, "config") as fake_config, \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        fake_config.get_prompt.return_value = "classify this: {query}"
        result = await tool_use.gather_via_tools("what's the status of SDLC-5?")

    assert result.mcp_unavailable is True
    assert result.is_empty is True


@pytest.mark.asyncio
async def test_mcp_down_and_query_is_pure_knowledge_stays_rag_only():
    fake_provider = MagicMock()
    fake_provider.generate_structured = AsyncMock(return_value=_FakeStructuredResponse(
        structured={"needs_live_data": False, "reason": "asks about a static checklist"}
    ))
    with patch.object(tool_use, "get_mcp_tools", AsyncMock(side_effect=RuntimeError("connection refused"))), \
         patch.object(tool_use, "config") as fake_config, \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        fake_config.get_prompt.return_value = "classify this: {query}"
        result = await tool_use.gather_via_tools("what's in the Clean Code checklist?")

    assert result.mcp_unavailable is False
    assert result.is_empty is True


@pytest.mark.asyncio
async def test_classification_failure_defaults_to_not_unavailable():
    """A broken classifier must not invent a new failure mode — default is today's RAG-only."""
    fake_provider = MagicMock()
    fake_provider.generate_structured = AsyncMock(side_effect=RuntimeError("rate limited"))
    with patch.object(tool_use, "get_mcp_tools", AsyncMock(return_value=[])), \
         patch.object(tool_use, "config") as fake_config, \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        fake_config.get_prompt.return_value = "classify this: {query}"
        result = await tool_use.gather_via_tools("what's the status of SDLC-5?")

    assert result.mcp_unavailable is False


@pytest.mark.asyncio
async def test_no_tool_calls_attempted_is_not_marked_unavailable():
    """Model decides it doesn't need a tool at all — legit RAG-only, no classification call."""
    done_message = AIMessage(content="DONE")
    fake_chat_model = MagicMock()
    fake_chat_model.bind_tools.return_value = fake_chat_model
    fake_chat_model.ainvoke = AsyncMock(return_value=done_message)
    fake_provider = MagicMock()
    fake_provider.get_chat_model.return_value = fake_chat_model

    with patch.object(tool_use, "get_mcp_tools", AsyncMock(return_value=[_fake_tool("jira_get_ticket")])), \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        result = await tool_use.gather_via_tools("what is the clean code checklist?")

    assert result.is_empty is True
    assert result.mcp_unavailable is False


@pytest.mark.asyncio
async def test_all_attempted_calls_failing_marks_unavailable():
    """The model itself decided it needed a tool — every attempt erroring out is MCP's fault, not 'no data needed'."""
    tool_call_msg = AIMessage(content="", tool_calls=[
        {"name": "jira_get_ticket", "args": {"ticket_id": "SDLC-5"}, "id": "1"},
    ])
    done_message = AIMessage(content="DONE")
    fake_chat_model = MagicMock()
    fake_chat_model.bind_tools.return_value = fake_chat_model
    fake_chat_model.ainvoke = AsyncMock(side_effect=[tool_call_msg, done_message])
    fake_provider = MagicMock()
    fake_provider.get_chat_model.return_value = fake_chat_model

    with patch.object(tool_use, "get_mcp_tools", AsyncMock(return_value=[_fake_tool("jira_get_ticket")])), \
         patch.object(tool_use, "ainvoke_tool", AsyncMock(side_effect=ConnectionError("refused"))), \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        result = await tool_use.gather_via_tools("what's the status of SDLC-5?")

    assert result.mcp_unavailable is True
    assert result.calls[0].error


@pytest.mark.asyncio
async def test_partial_failure_does_not_mark_unavailable():
    """One tool of two succeeds — real data came back, don't claim total outage."""
    tool_call_msg = AIMessage(content="", tool_calls=[
        {"name": "jira_get_ticket", "args": {"ticket_id": "SDLC-5"}, "id": "1"},
        {"name": "github_list_open_prs", "args": {}, "id": "2"},
    ])
    done_message = AIMessage(content="DONE")
    fake_chat_model = MagicMock()
    fake_chat_model.bind_tools.return_value = fake_chat_model
    fake_chat_model.ainvoke = AsyncMock(side_effect=[tool_call_msg, done_message])
    fake_provider = MagicMock()
    fake_provider.get_chat_model.return_value = fake_chat_model

    async def _invoke_side_effect(tool, args):
        if tool.name == "jira_get_ticket":
            raise ConnectionError("refused")
        return {"prs": []}

    with patch.object(tool_use, "get_mcp_tools", AsyncMock(return_value=[
            _fake_tool("jira_get_ticket"), _fake_tool("github_list_open_prs")])), \
         patch.object(tool_use, "ainvoke_tool", AsyncMock(side_effect=_invoke_side_effect)), \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        result = await tool_use.gather_via_tools("status of SDLC-5 and open PRs?")

    assert result.mcp_unavailable is False
    assert len(result.calls) == 2
