from __future__ import annotations

from backend.app.memory.extraction import extract_memory_candidates


# ── Positive ──────────────────────────────────────────────────────────────

def test_remember_that_my_x_is_y():
    assert extract_memory_candidates("remember that my favorite color is blue") == [("favorite_color", "blue")]


def test_remember_my_x_is_y_without_that():
    assert extract_memory_candidates("remember my name is Alex") == [("name", "Alex")]


def test_case_insensitive():
    assert extract_memory_candidates("REMEMBER THAT MY NAME IS ALEX") == [("name", "ALEX")]


def test_trailing_punctuation_is_stripped_from_value():
    assert extract_memory_candidates("remember that my name is Alex.") == [("name", "Alex")]


def test_does_not_require_the_word_my():
    assert extract_memory_candidates("remember the meeting is at 5pm") == [("the_meeting", "at 5pm")]


# ── Negative ──────────────────────────────────────────────────────────────

def test_message_without_remember_extracts_nothing():
    assert extract_memory_candidates("what is my favorite color?") == []


def test_remember_without_an_is_clause_extracts_nothing():
    assert extract_memory_candidates("please remember to buy milk") == []


def test_unrelated_message_extracts_nothing():
    assert extract_memory_candidates("what's the weather like today?") == []


# ── Edge ──────────────────────────────────────────────────────────────────

def test_empty_string_extracts_nothing():
    assert extract_memory_candidates("") == []


def test_value_that_is_only_punctuation_strips_to_empty_and_extracts_nothing():
    # `message.strip()` runs before regex matching, so trailing whitespace
    # never survives into the captured value — a "value is only whitespace"
    # input just fails to match at all (already covered by the "no match"
    # cases above). The value CAN still end up empty after post-processing
    # though: `.rstrip(".!")` on a value that's entirely punctuation (e.g.
    # someone typing "remember x is !!!") strips it down to nothing, which
    # is the actual reachable path to the "not value" guard.
    assert extract_memory_candidates("remember x is !!!") == []


def test_key_is_normalized_to_snake_case():
    assert extract_memory_candidates("remember that my favorite programming language is Python") == [
        ("favorite_programming_language", "Python")
    ]


# ── Side effects ────────────────────────────────────────────────────────

def test_extraction_is_pure_and_deterministic():
    message = "remember that my name is Alex"
    assert extract_memory_candidates(message) == extract_memory_candidates(message)
