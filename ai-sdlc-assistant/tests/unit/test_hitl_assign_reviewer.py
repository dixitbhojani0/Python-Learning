"""
tests/unit/test_hitl_assign_reviewer.py
Unit tests for _execute_assign_reviewer() — two bugs found from one real failure:
  1. hitl.py discarded the MCP call's result entirely and unconditionally reported
     "✅ assigned", even when GitHub rejected the request.
  2. The first fix for #1 GUESSED the failure reason (HTTP 422 -> "not a
     collaborator") — verified wrong: the real repo showed the reviewer WAS a
     collaborator, and the real GitHub error was actually a 403 "Resource not
     accessible by personal access token" (an under-scoped token, nothing to do
     with collaborator status). The fix now surfaces GitHub's own `message` field
     instead of guessing from the status code.
No Docker, no real GitHub (call_mcp_tool is mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.api.routes.hitl import _execute_assign_reviewer

_PROPOSAL = {"pr_number": "PR-5", "suggested_reviewer": "dixitbhojani-blip", "pr_title": "Nginx CORS fix"}


@pytest.mark.asyncio
async def test_success_reports_assigned():
    ok_result = {"pr": "PR-5", "reviewer": "dixitbhojani-blip", "status": "assigned"}
    with patch("backend.api.routes.hitl.call_mcp_tool", new=AsyncMock(return_value=ok_result)):
        text = await _execute_assign_reviewer(_PROPOSAL)
    assert "✅" in text
    assert "assigned to PR-5" in text


@pytest.mark.asyncio
async def test_github_rejection_surfaces_githubs_real_message_not_a_guess():
    """
    The exact real-world case: reviewer WAS a collaborator, GitHub returned 403
    "Resource not accessible by personal access token" (a token-scope problem).
    The confirmation must show that real message, not claim success, and not
    invent an unrelated "not a collaborator" explanation.
    """
    error_result = {
        "pr": "PR-5", "reviewer": "dixitbhojani-blip", "status": "error",
        "http_code": 403, "message": "Resource not accessible by personal access token",
    }
    with patch("backend.api.routes.hitl.call_mcp_tool", new=AsyncMock(return_value=error_result)):
        text = await _execute_assign_reviewer(_PROPOSAL)
    assert "❌" in text
    assert "Resource not accessible by personal access token" in text
    assert "not a collaborator" not in text.lower()  # the old, disproven guess must be gone
    assert "✅" not in text
    assert "assigned to PR-5" not in text


@pytest.mark.asyncio
async def test_github_silent_2xx_omission_reports_failure_not_false_success():
    """
    A third, distinct failure mode found live: GitHub returns 201 Created even
    when the reviewer username doesn't exist — it just silently omits them from
    requested_reviewers instead of erroring. The connector now checks the response
    body for this and reports it as an error; this confirms hitl.py surfaces that
    correctly rather than trusting the 2xx status code alone.
    """
    error_result = {
        "pr": "PR-6", "reviewer": "randomuser123", "status": "error", "http_code": 201,
        "message": "GitHub accepted the request but did not add 'randomuser123' as a reviewer "
                   "— the user likely doesn't exist or can't be reached for this repo.",
    }
    with patch("backend.api.routes.hitl.call_mcp_tool", new=AsyncMock(return_value=error_result)):
        text = await _execute_assign_reviewer({"pr_number": "PR-6", "suggested_reviewer": "randomuser123", "pr_title": "DB pool fix"})
    assert "❌" in text
    assert "did not add" in text
    assert "✅" not in text
    assert "assigned to PR-6" not in text


@pytest.mark.asyncio
async def test_connector_unavailable_reports_failure_not_false_success():
    error_result = {"pr": "PR-5", "reviewer": "dixitbhojani-blip", "status": "error", "http_code": 0, "message": ""}
    with patch("backend.api.routes.hitl.call_mcp_tool", new=AsyncMock(return_value=error_result)):
        text = await _execute_assign_reviewer(_PROPOSAL)
    assert "⚠️" in text
    assert "✅" not in text


@pytest.mark.asyncio
async def test_unassigned_reviewer_raises_400_instead_of_calling_github():
    with pytest.raises(HTTPException) as exc_info:
        await _execute_assign_reviewer({"pr_number": "PR-5", "suggested_reviewer": "unassigned"})
    assert exc_info.value.status_code == 400
