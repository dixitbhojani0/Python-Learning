"""
tests/unit/test_pr_review_agent_reject.py
Unit tests for PRReviewAgent's "request changes" intent (reject_pr) — the gap
found during the codebase audit: github_request_changes_pr existed as a real
MCP tool but no agent ever proposed it and no HITL handler ever executed it.
No Docker, no LLM, no MCP (call_mcp_tool + the LLM/retriever are mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pr_review_agent import PRReviewAgent

_PRS = [{
    "id": "PR-5", "title": "Fix nginx CORS headers", "author": "alice",
    "status": "OPEN", "ci_status": "passed", "files_changed": ["nginx.conf"],
    "reviewers": [], "branch": "fix/cors", "base_branch": "main", "description": "",
}]

_REVIEW_DATA = {
    "pr_number": "PR-5", "pr_title": "Fix nginx CORS headers",
    "files_changed": "nginx.conf", "ci_status": "passed",
    "standards_result": "PASS", "version_policy_result": "COMPLIANT",
    "concerns": "Missing test coverage for the new header logic.",
    "suggested_reviewer": "unassigned", "risk_level": "MEDIUM", "summary": "",
}


class _FakeConfig:
    def get_prompt(self, key, **kwargs):
        return f"prompt:{key}"

    def get_temperature(self, key):
        return 0.1


class _FakeLLMResponse:
    def __init__(self, structured):
        self.structured = structured
        self.parse_error = False


class _FakeLLM:
    async def generate_structured(self, *a, **kw):
        # A fresh copy each call — the agent mutates review_data in place, which
        # would otherwise leak between tests sharing this same dict object.
        return _FakeLLMResponse(dict(_REVIEW_DATA))


class _FakeRetriever:
    def retrieve(self, query, project, doc_types=None):
        return [], 0.5


def _agent() -> PRReviewAgent:
    agent = PRReviewAgent.__new__(PRReviewAgent)  # skip __init__ — inject fakes directly
    agent.config    = _FakeConfig()
    agent.llm       = _FakeLLM()
    agent.retriever = _FakeRetriever()
    return agent


async def _run(query: str):
    agent = _agent()
    state = {"query": query, "project_id": "SDLC", "user_role": "developer"}
    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=AsyncMock(return_value=_PRS)):
        return await agent.run(state)


@pytest.mark.asyncio
async def test_request_changes_proposes_reject_pr_action():
    payload = await _run("request changes on PR-5, tests are failing")
    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "reject_pr"
    assert payload.hitl_proposal["pr_number"] == "PR-5"


@pytest.mark.asyncio
async def test_reject_keyword_also_proposes_reject_pr_action():
    payload = await _run("reject PR-5")
    assert payload.hitl_proposal["action"] == "reject_pr"


@pytest.mark.asyncio
async def test_approve_still_proposes_approve_pr_not_reject():
    """Regression guard: the new pattern must not swallow the existing approve intent."""
    payload = await _run("approve PR-5")
    assert payload.hitl_proposal["action"] == "approve_pr"


@pytest.mark.asyncio
async def test_request_changes_card_shows_the_concerns_as_review_body():
    payload = await _run("request changes on PR-5")
    assert "Missing test coverage" in payload.structured["final_response"]
    assert "request changes" in payload.structured["final_response"].lower()
