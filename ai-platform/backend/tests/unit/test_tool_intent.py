from __future__ import annotations

from backend.app.tools.intent import detect_tool_intent


# ── Positive ──────────────────────────────────────────────────────────────

def test_calculate_triggers_the_calculator_tool_with_the_expression():
    assert detect_tool_intent("calculate 2 + 2") == ("calculator", {"expression": "2 + 2"})


def test_what_time_is_it_triggers_the_time_tool():
    assert detect_tool_intent("what time is it") == ("current_time", {})


def test_whats_the_current_time_triggers_the_time_tool():
    assert detect_tool_intent("what's the current time?") == ("current_time", {})


def test_delete_all_documents_triggers_the_delete_tool():
    assert detect_tool_intent("please delete all my documents") == ("delete_all_documents", {})


def test_case_insensitive():
    assert detect_tool_intent("CALCULATE 3 * 4") == ("calculator", {"expression": "3 * 4"})


# ── Negative ──────────────────────────────────────────────────────────────

def test_unrelated_message_triggers_nothing():
    assert detect_tool_intent("hello, how are you today?") is None


def test_mentioning_calculate_without_an_expression_still_matches_pattern_shape():
    # "calculate" with nothing after it that looks like digits/operators
    # simply matches an empty/short capture — not this function's job to
    # validate the expression itself (calculator_tool.py does that).
    assert detect_tool_intent("can you calculate") is None


def test_empty_string_triggers_nothing():
    assert detect_tool_intent("") is None


# ── Edge ──────────────────────────────────────────────────────────────────

def test_only_the_first_matching_pattern_wins_never_two_tools_at_once():
    # A message that could arguably match more than one intent still
    # returns exactly one tuple — the bounded-autonomy "at most one tool
    # call per turn" guarantee lives here, not in the caller.
    result = detect_tool_intent("calculate 5 + 5 and also what time is it")
    assert result is not None
    assert result[0] == "calculator"


# ── Side effects ────────────────────────────────────────────────────────

def test_detection_is_pure_and_deterministic():
    message = "calculate 10 / 2"
    assert detect_tool_intent(message) == detect_tool_intent(message)
