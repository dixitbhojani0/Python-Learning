"""
tests/unit/test_notify_agent_dynamic_compose.py
Unit tests for NotifyAgent's dynamic gather-and-compose fallback — the answer to
"can we send ANY kind of message?" Previously only two paths existed: literal
text given, or the one hardcoded sprint_status compose function; anything else
("notify X about the blocked tickets") always fell straight to "please include
the message". Now it runs the same read-only tool-gathering loop MCPAgent uses,
then composes a message from whatever it found — only falling back to asking
the user when gathering finds nothing relevant.
No Docker, no LLM, no MCP (gather_via_tools and the LLM extraction are mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.notify_agent import _NOTIFY_GATHER_SYSTEM, NotifyAgent
from backend.mcp_client.tool_use import ToolCall, ToolGatherResult


class _FakeConfig:
    def get_agent(self, key):
        return {"slack_channel": "#engineering-manager"}

    def get_prompt(self, key, **kwargs):
        return f"prompt:{key}:{kwargs.get('query', '')}"


class _FakeLLMResponse:
    def __init__(self, structured=None, text=""):
        self.structured  = structured or {}
        self.text        = text
        self.parse_error = structured is None


class _FakeLLM:
    def __init__(self, extraction: dict, composed_text: str = ""):
        self._extraction = extraction
        self._composed   = composed_text

    async def generate_structured(self, *a, **kw):
        return _FakeLLMResponse(structured=self._extraction)

    async def generate_text(self, *a, **kw):
        return _FakeLLMResponse(text=self._composed)


def _agent(extraction: dict, composed_text: str = "") -> NotifyAgent:
    agent = NotifyAgent.__new__(NotifyAgent)
    agent.config    = _FakeConfig()
    agent.llm       = _FakeLLM(extraction, composed_text)
    agent.retriever = None
    return agent


@pytest.mark.asyncio
async def test_topic_with_gathered_data_composes_a_message_instead_of_asking():
    """The exact reported gap: 'about the blocked tickets' has no literal text
    and isn't sprint_status, but real data IS gatherable — must not ask for
    clarification when there's something real to compose from."""
    agent = _agent(
        extraction={"channel": "engineering-manager", "message": "", "intent": "unclear"},
        composed_text="Blocked: SDLC-3 (CORS fix) is stuck on vendor SSL renewal.",
    )
    gathered = ToolGatherResult(calls=[ToolCall("jira_get_blocked_tickets", {}, [{"id": "SDLC-3"}])])
    state = {"query": "notify the managers about the blocked tickets", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock(return_value=gathered)):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["message"] == "Blocked: SDLC-3 (CORS fix) is stuck on vendor SSL renewal."
    assert payload.hitl_proposal["channel"] == "engineering-manager"


@pytest.mark.asyncio
async def test_topic_with_nothing_gathered_still_asks_for_clarification():
    """Gathering must not fabricate a message when there's genuinely nothing to find."""
    agent = _agent(extraction={"channel": "general", "message": "", "intent": "unclear"})
    empty = ToolGatherResult()
    state = {"query": "notify general about something vague", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock(return_value=empty)):
        payload = await agent.run(state)

    assert payload.hitl_required is False
    assert "please include the message" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_all_tool_calls_erroring_still_asks_for_clarification():
    """
    The exact live bug found: a topic with no real subject can still make the
    gather loop call an irrelevant tool that errors (verified live — it tried
    resolving "general" as a Slack channel and got a lookup failure). is_empty
    alone doesn't catch this since a call WAS made; every call being an error
    must be treated the same as no calls at all.
    """
    agent = _agent(extraction={"channel": "general", "message": "", "intent": "unclear"})
    all_errors = ToolGatherResult(calls=[ToolCall("slack_get_channel_history", {"channel": "general"}, None, error="channel not found")])
    state = {"query": "notify general about the weather today", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock(return_value=all_errors)):
        payload = await agent.run(state)

    assert payload.hitl_required is False
    assert "please include the message" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_none_sentinel_from_compose_step_falls_back_to_clarification():
    """The compose prompt is told to output NONE when data isn't actually usable
    — confirm the agent honours that instead of sending "NONE" as a message."""
    agent = _agent(
        extraction={"channel": "general", "message": "", "intent": "unclear"},
        composed_text="NONE",
    )
    gathered = ToolGatherResult(calls=[ToolCall("jira_search_tickets", {}, [])])
    state = {"query": "notify general about nothing in particular", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock(return_value=gathered)):
        payload = await agent.run(state)

    assert payload.hitl_required is False
    assert "please include the message" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_noisy_failed_calls_alongside_real_data_still_compose_a_message():
    """
    The exact regression reported after the first fix: "notify the managers
    about the blocked tickets" gathered ONE real, useful result (a blocked
    ticket) alongside several irrelevant failed calls (the gather loop trying
    to resolve "managers" as a Slack search term, each coming back as a
    domain-error dict with no ToolCall.error set). That noise sitting next to
    real data must not make the compose step decline — only calls that are
    error-shaped should be filtered out before composing.
    """
    agent = _agent(
        extraction={"channel": "engineering-manager", "message": "", "intent": "unclear"},
        composed_text="Blocked: SDLC-3 (CORS fix) is unassigned and marked highest priority.",
    )
    mixed = ToolGatherResult(calls=[
        ToolCall("jira_get_blocked_tickets", {}, [{"id": "SDLC-3"}]),
        ToolCall("slack_search_messages", {"query": "manager"}, {"error": "channel '#manager' not found"}),
        ToolCall("slack_search_messages", {"query": "managers"}, {"error": "channel '#managers' not found"}),
    ])
    state = {"query": "notify the managers about the blocked tickets", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock(return_value=mixed)):
        payload = await agent.run(state)

    assert payload.hitl_required is True
    assert payload.hitl_proposal["message"] == "Blocked: SDLC-3 (CORS fix) is unassigned and marked highest priority."


@pytest.mark.asyncio
async def test_gather_is_scoped_to_topic_not_audience():
    """Guard against the custom gather system prompt being silently dropped —
    without it, the gather loop tries to search Slack for "the managers" as
    if it were the thing to investigate, instead of treating the audience as
    already resolved via channel extraction."""
    agent = _agent(extraction={"channel": "engineering-manager", "message": "", "intent": "unclear"})
    gathered = ToolGatherResult(calls=[ToolCall("jira_get_blocked_tickets", {}, [{"id": "SDLC-3"}])])
    state = {"query": "notify the managers about the blocked tickets", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock(return_value=gathered)) as mock_gather:
        await agent.run(state)

    mock_gather.assert_called_once_with(state["query"], system=_NOTIFY_GATHER_SYSTEM)


@pytest.mark.asyncio
async def test_literal_message_never_triggers_gathering():
    """Regression guard: the fast literal-text path must not be bypassed by gathering."""
    agent = _agent(extraction={"channel": "backend", "message": "PR-5 needs review", "intent": "custom"})
    state = {"query": "notify #backend: PR-5 needs review", "project_id": "SDLC", "user_id": "dixit"}

    with patch("backend.agents.notify_agent.gather_via_tools", new=AsyncMock()) as mock_gather:
        payload = await agent.run(state)

    mock_gather.assert_not_called()
    assert payload.hitl_proposal["message"] == "PR-5 needs review"
