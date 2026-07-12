"""
tests/unit/test_pr_review_agent_already_assigned.py
Unit tests for PRReviewAgent's "already assigned" guard — mirrors ticket_agent's
existing guard for Jira ticket assignment. Without it, "assign reviewer X to PR-N"
re-proposes (and re-submits, on approval) an assignment that's already in effect,
which is exactly what the user hit: PR-5 already had dixitbhojani-blip as a
requested reviewer, and the app still built a fresh "shall I assign them?" card.
No Docker, no LLM, no MCP (call_mcp_tool + the LLM/retriever are mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pr_review_agent import PRReviewAgent

_REVIEW_DATA = {
    "pr_number": "PR-5", "pr_title": "Fix nginx CORS headers",
    "files_changed": "nginx.conf", "ci_status": "passed",
    "standards_result": "PASS", "version_policy_result": "COMPLIANT",
    "concerns": "", "suggested_reviewer": "unassigned", "risk_level": "MEDIUM", "summary": "",
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
        # A fresh copy each call — the agent mutates review_data in place
        # (e.g. review_data["suggested_reviewer"] = ...), which would otherwise
        # leak between tests since they'd all share the same dict object.
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


async def _run(query: str, reviewers: list[str]):
    agent = _agent()
    prs = [{
        "id": "PR-5", "title": "Fix nginx CORS headers", "author": "alice",
        "status": "OPEN", "ci_status": "passed", "files_changed": ["nginx.conf"],
        "reviewers": reviewers, "branch": "fix/cors", "base_branch": "main", "description": "",
    }]
    state = {"query": query, "project_id": "SDLC", "user_role": "developer"}
    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=AsyncMock(return_value=prs)):
        return await agent.run(state)


@pytest.mark.asyncio
async def test_already_assigned_reviewer_skips_hitl():
    payload = await _run("assign reviewer dixitbhojani-blip to PR-5", reviewers=["dixitbhojani-blip"])
    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "already" in payload.structured["final_response"].lower()
    assert "PR-5" in payload.structured["final_response"]


@pytest.mark.asyncio
async def test_already_assigned_check_is_case_insensitive():
    payload = await _run("assign reviewer Dixitbhojani-Blip to PR-5", reviewers=["dixitbhojani-blip"])
    assert payload.hitl_required is False


@pytest.mark.asyncio
async def test_not_yet_assigned_reviewer_still_proposes_hitl():
    """Regression guard: a genuinely new reviewer must still get a real proposal."""
    payload = await _run("assign reviewer alice to PR-5", reviewers=["dixitbhojani-blip"])
    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "assign_reviewer"
    assert payload.hitl_proposal["suggested_reviewer"] == "alice"


@pytest.mark.asyncio
async def test_plain_review_request_still_shows_the_full_review_card():
    """
    The exact regression: "review PR-X" is a request for the REVIEW (standards,
    CI, risk), not for a reviewer-assignment decision. The first version of the
    already-assigned guard short-circuited with a one-line message and threw away
    the entire review card whenever the auto-suggested reviewer happened to already
    be assigned — even though the user never asked to assign anyone.
    """
    payload = await _run("Review PR-5", reviewers=["dixitbhojani-blip"])
    text = payload.structured["final_response"]
    assert "Coding Standards" in text
    assert "CI Status" in text
    assert "Overall Risk" in text
    assert "already the requested reviewer" in text.lower()
    assert payload.hitl_required is False
