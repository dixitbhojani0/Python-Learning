"""
tests/unit/test_pr_review_agent_concise.py
Unit tests for the concise-vs-full response distinction in PRReviewAgent: naming
a specific reviewer to assign is an explicit action request (concise: no
standards/CI/risk table, just the assign outcome), while "review PR-X" or a
bare "assign a reviewer" (no name given) is treated as wanting the review itself,
with any reviewer suggestion as a secondary note (full card).
No Docker, no LLM, no MCP (call_mcp_tool is mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pr_review_agent import PRReviewAgent

_REVIEW_DATA = {
    "pr_number": "PR-6", "pr_title": "DB connection pool Fix",
    "files_changed": "config/db-pool.yaml", "ci_status": "unknown",
    "standards_result": "PASS", "version_policy_result": "COMPLIANT",
    "concerns": "", "suggested_reviewer": "alice", "risk_level": "MEDIUM", "summary": "",
}

_PRS = [{
    "id": "PR-6", "title": "DB connection pool Fix", "author": "bob",
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


async def _mcp(tool_name, args):
    if tool_name == "github_is_collaborator":
        return {"username": args["username"], "is_collaborator": True}
    return _PRS


async def _run(query: str):
    agent = _agent()
    state = {"query": query, "project_id": "SDLC", "user_role": "developer"}
    with patch("backend.agents.pr_review_agent.call_mcp_tool", new=AsyncMock(side_effect=_mcp)):
        return await agent.run(state)


@pytest.mark.asyncio
async def test_explicit_reviewer_named_gets_concise_response():
    payload = await _run("Assign reviewer alice to PR-6")
    text = payload.structured["final_response"]
    assert payload.hitl_required is True
    assert "Coding Standards" not in text
    assert "CI Status" not in text
    assert "Shall I assign `alice`" in text


@pytest.mark.asyncio
async def test_plain_review_request_gets_full_card():
    payload = await _run("Review PR-6")
    text = payload.structured["final_response"]
    assert "Coding Standards" in text
    assert "CI Status" in text


@pytest.mark.asyncio
async def test_bare_assign_request_with_no_name_gets_full_card():
    """No reviewer named -> auto-suggested -> full card justifies the suggestion."""
    payload = await _run("assign a reviewer to PR-6")
    text = payload.structured["final_response"]
    assert "Coding Standards" in text
