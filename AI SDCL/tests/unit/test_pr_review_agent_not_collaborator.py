"""
tests/unit/test_pr_review_agent_not_collaborator.py
Unit tests for PRReviewAgent's "not a collaborator" guard — validates a suggested
reviewer BEFORE proposing to assign them. Without this, "assign reviewer
randomuser123 to PR-6" built a full "Click Approve" card for a username that
doesn't exist, and GitHub's assign-reviewer API silently ignores invalid
usernames (returns 201, just omits them) instead of erroring — so the failure
only ever surfaced after the user clicked Approve.
No Docker, no LLM, no real GitHub (call_mcp_tool is mocked with a side_effect
that distinguishes the PR-fetch calls from the new collaborator-check call).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pr_review_agent import PRReviewAgent

_REVIEW_DATA = {
    "pr_number": "PR-6", "pr_title": "DB connection pool Fix",
    "files_changed": "config/db-pool.yaml", "ci_status": "unknown",
    "standards_result": "PASS", "version_policy_result": "COMPLIANT",
    "concerns": "", "suggested_reviewer": "unassigned", "risk_level": "MEDIUM", "summary": "",
}

_PRS = [{
    "id": "PR-6", "title": "DB connection pool Fix", "author": "alice",
    "status": "OPEN", "ci_status": "unknown", "files_changed": ["config/db-pool.yaml"],
    "reviewers": [], "branch": "fix/db-pool", "base_branch": "main", "description": "",
}]


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


def _mock_mcp(known_collaborators: set[str]):
    async def _call(tool_name, args):
        if tool_name == "github_is_collaborator":
            return {"username": args["username"], "is_collaborator": args["username"] in known_collaborators}
        return _PRS  # github_search_prs / github_list_open_prs
    return AsyncMock(side_effect=_call)


async def _run(query: str, known_collaborators: set[str]):
    agent = _agent()
    state = {"query": query, "project_id": "SDLC", "user_role": "developer"}
    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=_mock_mcp(known_collaborators)):
        return await agent.run(state)


@pytest.mark.asyncio
async def test_non_collaborator_skips_hitl_with_a_concise_message():
    """
    The exact reported bug: fake username must not get a 'Click Approve' card.
    Response is concise (no standards/CI/risk table) — the user named a specific
    reviewer to assign, an explicit action request, not a review request. See
    test_pr_review_agent_ambiguous_target.py / the "concise" design discussion for
    why explicit-reviewer queries skip the full review card.
    """
    payload = await _run("Assign reviewer randomuser123 to PR-6", known_collaborators={"dixitbhojani-blip"})
    text = payload.structured["final_response"]
    assert payload.hitl_required is False
    assert payload.hitl_proposal == {}
    assert "not a collaborator" in text.lower()
    assert "Click **Approve**" not in text
    assert "Coding Standards" not in text
    assert "CI Status" not in text


@pytest.mark.asyncio
async def test_real_collaborator_still_gets_a_real_proposal():
    """Regression guard: a genuine collaborator must not be blocked by this check."""
    payload = await _run("Assign reviewer dixitbhojani-blip to PR-6", known_collaborators={"dixitbhojani-blip"})
    assert payload.hitl_required is True
    assert payload.hitl_proposal["action"] == "assign_reviewer"
    assert payload.hitl_proposal["suggested_reviewer"] == "dixitbhojani-blip"
    assert "Click **Approve**" in payload.structured["final_response"]


@pytest.mark.asyncio
async def test_collaborator_check_failure_fails_open_not_blocking():
    """A broken check must not block a legitimate assignment — fail open, not closed."""
    agent = _agent()
    state = {"query": "Assign reviewer dixitbhojani-blip to PR-6", "project_id": "SDLC", "user_role": "developer"}

    async def _raising_call(tool_name, args):
        if tool_name == "github_is_collaborator":
            raise RuntimeError("network blip")
        return _PRS

    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=AsyncMock(side_effect=_raising_call)):
        payload = await agent.run(state)
    assert payload.hitl_required is True
