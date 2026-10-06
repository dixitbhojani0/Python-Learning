"""
tests/unit/test_chat_hitl_action_type.py

E8 — unit tests for chat.py's _hitl_action_type(), the pure helper that
exposes a pending HITL proposal's action type on ChatResponse so the
frontend can decide whether to offer "approve all" without re-deriving it
from response text. Mirrors test_chat_trace.py's pure-function test pattern.
"""
from backend.api.routes.chat import _hitl_action_type


def test_returns_none_when_no_hitl_pending():
    result = {"hitl_proposal": {"action": "send_slack", "channel": "backend"}}
    assert _hitl_action_type(result, hitl_required=False) is None


def test_returns_action_type_when_hitl_pending():
    result = {"hitl_proposal": {"action": "send_slack", "channel": "backend"}}
    assert _hitl_action_type(result, hitl_required=True) == "send_slack"


def test_returns_none_when_proposal_missing_despite_hitl_flag():
    """Defensive: hitl_required=True with no/empty proposal must not crash."""
    assert _hitl_action_type({}, hitl_required=True) is None
    assert _hitl_action_type({"hitl_proposal": {}}, hitl_required=True) is None
