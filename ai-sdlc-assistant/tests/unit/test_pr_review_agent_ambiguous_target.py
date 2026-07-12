"""
tests/unit/test_pr_review_agent_ambiguous_target.py
Unit tests for PRReviewAgent's "which PR did you mean?" guard. Without it,
"assign dixitbhojani-blip as reviewer" (no PR number, 5 open PRs) silently let
the LLM pick one PR out of the whole batch and proposed assigning there — the
user never asked about that specific PR and had no way to know a different one
was chosen. No Docker, no LLM, no MCP (call_mcp_tool is mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pr_review_agent import PRReviewAgent

_REVIEW_DATA = {
    "pr_number": "PR-4", "pr_title": "Dashboard API integration tests",
    "files_changed": "test_dashboard_api.py", "ci_status": "unknown",
    "standards_result": "PASS", "version_policy_result": "COMPLIANT",
    "concerns": "", "suggested_reviewer": "unassigned", "risk_level": "MEDIUM", "summary": "",
}

_MANY_PRS = [
    {"id": "PR-4", "title": "Dashboard API integration tests", "author": "alice", "status": "OPEN",
     "ci_status": "unknown", "files_changed": [], "reviewers": [], "branch": "b4", "base_branch": "main", "description": ""},
    {"id": "PR-1", "title": "Information Reports", "author": "alice", "status": "OPEN",
     "ci_status": "unknown", "files_changed": [], "reviewers": [], "branch": "b1", "base_branch": "main", "description": ""},
    {"id": "PR-5", "title": "Nginx CORS header fix", "author": "alice", "status": "OPEN",
     "ci_status": "unknown", "files_changed": [], "reviewers": [], "branch": "b5", "base_branch": "main", "description": ""},
]


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
        return _FakeLLMResponse(dict(_REVIEW_DATA))


class _FakeRetriever:
    def retrieve(self, query, project, doc_types=None):
        return [], 0.5


def _agent() -> PRReviewAgent:
    agent = PRReviewAgent.__new__(PRReviewAgent)
    agent.config    = _FakeConfig()
    agent.llm       = _FakeLLM()
    agent.retriever = _FakeRetriever()
    return agent


async def _run(query: str):
    agent = _agent()
    state = {"query": query, "project_id": "SDLC", "user_role": "developer"}
    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=AsyncMock(return_value=_MANY_PRS)):
        return await agent.run(state)


@pytest.mark.asyncio
async def test_assign_with_no_pr_number_and_multiple_open_prs_asks_which_one():
    """The exact reported bug: no PR named, 5 open PRs, must not silently pick one."""
    payload = await _run("assign dixitbhojani-blip as reviewer")
    text = payload.structured["final_response"]
    assert payload.hitl_required is False
    assert "which pr" in text.lower()
    assert "PR-4" in text and "PR-1" in text and "PR-5" in text


@pytest.mark.asyncio
async def test_approve_with_no_pr_number_and_multiple_open_prs_asks_which_one():
    payload = await _run("approve it")
    assert payload.hitl_required is False
    assert "which pr" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_assign_with_explicit_pr_number_is_unaffected():
    """Regression guard: naming the PR must skip the ambiguity check entirely."""
    payload = await _run("assign dixitbhojani-blip as reviewer to PR-5")
    assert "which pr" not in payload.structured["final_response"].lower()
    assert payload.hitl_required is True
    assert payload.hitl_proposal.get("action") == "assign_reviewer"
