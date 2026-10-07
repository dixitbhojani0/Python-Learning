"""
tests/unit/test_hitl_deassign_ticket.py
Unit tests for _execute_deassign_ticket() (E7) + the deassign_ticket permission tier.
No Docker, no real Jira (call_mcp_tool is mocked).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.api.routes.hitl import _execute_deassign_ticket
from backend.auth.permissions import can

_PROPOSAL = {"action": "deassign_ticket", "ticket_id": "SDLC-5", "assignee": "Alice", "project": "SDLC"}


@pytest.mark.asyncio
async def test_success_reports_unassigned():
    with patch("backend.orchestrator.actions.call_mcp_tool", new=AsyncMock(return_value={"success": True, "ticket_id": "SDLC-5"})):
        text = await _execute_deassign_ticket(_PROPOSAL, "Bob")
    assert "✅" in text
    assert "SDLC-5" in text
    assert "unassigned" in text.lower()
    assert "Alice" in text  # shows who it was previously assigned to


@pytest.mark.asyncio
async def test_mcp_failure_reports_warning_not_false_success():
    with patch("backend.orchestrator.actions.call_mcp_tool", new=AsyncMock(return_value={"success": False, "error": "HTTP 404"})):
        text = await _execute_deassign_ticket(_PROPOSAL, "Bob")
    assert "⚠️" in text
    assert "✅" not in text


@pytest.mark.asyncio
async def test_mcp_exception_reports_warning_not_raises():
    with patch("backend.orchestrator.actions.call_mcp_tool", new=AsyncMock(side_effect=RuntimeError("boom"))):
        text = await _execute_deassign_ticket(_PROPOSAL, "Bob")
    assert "⚠️" in text


def test_stakeholder_cannot_deassign():
    assert can("stakeholder", "deassign_ticket") is False


def test_developer_can_deassign():
    assert can("developer", "deassign_ticket") is True
