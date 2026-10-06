"""
tests/unit/test_hitl_approval_event_text.py

E8 — unit test for hitl.py's _approval_event_text(), the episodic-memory
audit-trail line for an approved HITL action. Extracted (same precedent as
_execute_assign_reviewer) so the "remember" note can be tested without
invoking the full rate-limited /api/hitl/approve route.
"""
from backend.api.routes.hitl import _approval_event_text


def test_plain_approval_has_no_remembered_note():
    text = _approval_event_text("Alice", "send_slack", "Message sent to #backend.", remember=False)
    assert text == "Alice approved send_slack: Message sent to #backend."
    assert "remembered" not in text


def test_remembered_approval_notes_session_scope():
    text = _approval_event_text("Alice", "send_slack", "Message sent to #backend.", remember=True)
    assert text == "Alice approved send_slack (remembered for this session): Message sent to #backend."


def test_long_result_text_still_truncated_to_200_chars():
    long_result = "x" * 500
    text = _approval_event_text("Bob", "create_ticket", long_result, remember=True)
    assert text.count("x") == 200
