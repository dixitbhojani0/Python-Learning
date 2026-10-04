"""
tests/unit/test_pr_review_agent_history_resolution.py
Unit tests for PRReviewAgent's pronoun/PR-ID resolution from session history (B10 gap).

Before this fix: "approve it" / "assign alice as reviewer" with no explicit PR number
and multiple open PRs always asked "which PR did you mean?" — even immediately after
"review PR-5" in the same session, where the answer was already in recent_messages.
No Docker, no real LLM/MCP (call_mcp_tool and the LLM are faked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pr_review_agent import PRReviewAgent

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
    def __init__(self, pr_number: str):
        self._pr_number = pr_number

    async def generate_structured(self, *a, **kw):
        return _FakeLLMResponse({
            "pr_number": self._pr_number, "pr_title": "x",
            "files_changed": "", "ci_status": "unknown",
            "standards_result": "PASS", "version_policy_result": "COMPLIANT",
            "concerns": "", "suggested_reviewer": "unassigned", "risk_level": "MEDIUM", "summary": "",
        })


class _FakeRetriever:
    def retrieve(self, query, project, doc_types=None):
        return [], 0.5


def _agent(pr_number: str) -> PRReviewAgent:
    agent = PRReviewAgent.__new__(PRReviewAgent)
    agent.config    = _FakeConfig()
    agent.llm       = _FakeLLM(pr_number)
    agent.retriever = _FakeRetriever()
    return agent


async def _run(query: str, recent_messages: list[dict], pr_number: str = "PR-5"):
    agent = _agent(pr_number)
    state = {"query": query, "project_id": "SDLC", "user_role": "developer", "recent_messages": recent_messages}
    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=AsyncMock(return_value=_MANY_PRS)):
        return await agent.run(state)


@pytest.mark.asyncio
async def test_approve_it_resolves_pr_from_recent_history():
    """The exact gap: 'approve it' must use the PR just discussed, not ask which one."""
    history = [{"query": "review PR-5", "response": "PR review for PR-5 ...", "role": "developer"}]
    payload = await _run("approve it", history)

    assert "which pr" not in payload.structured["final_response"].lower()
    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "approve_pr"
    assert payload.hitl_proposal["pr_number"] == "PR-5"


@pytest.mark.asyncio
async def test_assign_reviewer_with_no_pr_number_resolves_from_history():
    history = [{"query": "what's the status of PR-1?", "response": "PR-1 is open, CI passed.", "role": "developer"}]
    payload = await _run("assign alice as reviewer", history, pr_number="PR-1")

    assert "which pr" not in payload.structured["final_response"].lower()
    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "assign_reviewer"
    assert payload.hitl_proposal["pr_number"] == "PR-1"
    assert payload.hitl_proposal["suggested_reviewer"] == "alice"


@pytest.mark.asyncio
async def test_no_matching_history_still_asks_which_one():
    """Regression: history with no PR id at all must fall back to the ambiguity guard."""
    history = [{"query": "what's the sprint status?", "response": "70% complete.", "role": "developer"}]
    payload = await _run("approve it", history)

    assert payload.hitl_required is False
    assert "which pr" in payload.structured["final_response"].lower()


@pytest.mark.asyncio
async def test_explicit_pr_in_query_overrides_history():
    """An explicit PR id in the current query always wins over an older one in history."""
    history = [{"query": "review PR-5", "response": "PR review for PR-5 ...", "role": "developer"}]
    payload = await _run("approve PR-1", history, pr_number="PR-1")

    assert payload.hitl_proposal["pr_number"] == "PR-1"
