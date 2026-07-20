"""
tests/unit/test_release_readiness_agent.py
Unit tests for _format_release_response() — specifically that NO_GO no longer
offers Approve/Reject buttons (hitl.py hard-blocks approving a NO_GO anyway,
so those buttons were a dead-end). No Docker, no LLM, no MCP.
"""
from backend.agents.release_readiness_agent import _format_release_response


def test_go_verdict_shows_approve_reject():
    text = _format_release_response({"verdict": "GO", "confidence": 0.9})
    assert "Click **Approve**" in text
    assert "Reject" in text


def test_no_go_verdict_has_no_approve_reject_buttons():
    text = _format_release_response({"verdict": "NO_GO", "confidence": 0.9, "blockers": ["SDLC-3"]})
    assert "Click **Approve**" not in text
    assert "re-run the release readiness check" in text
