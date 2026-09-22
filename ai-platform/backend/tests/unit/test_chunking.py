from __future__ import annotations

import pytest

from backend.app.rag.chunking import chunk_text


# ── Positive ──────────────────────────────────────────────────────────────

def test_text_shorter_than_chunk_size_is_a_single_chunk():
    text = "one two three"
    assert chunk_text(text, chunk_size=10, overlap=2) == ["one two three"]


def test_long_text_produces_overlapping_chunks():
    words = [f"w{i}" for i in range(25)]
    text = " ".join(words)

    chunks = chunk_text(text, chunk_size=10, overlap=3)

    assert len(chunks) > 1
    # The last `overlap` words of one chunk must equal the first `overlap`
    # words of the next — that's the entire point of overlap (context isn't
    # cut off cleanly at a chunk boundary).
    first_words = chunks[0].split()
    second_words = chunks[1].split()
    assert first_words[-3:] == second_words[:3]


def test_last_chunk_covers_the_end_of_the_text_exactly_once():
    words = [f"w{i}" for i in range(25)]
    text = " ".join(words)

    chunks = chunk_text(text, chunk_size=10, overlap=3)

    assert chunks[-1].split()[-1] == "w24"  # the final word is present exactly at the end


# ── Negative ──────────────────────────────────────────────────────────────

def test_overlap_greater_than_or_equal_to_chunk_size_raises():
    with pytest.raises(ValueError):
        chunk_text("some text here", chunk_size=5, overlap=5)


# ── Edge ──────────────────────────────────────────────────────────────────

def test_empty_string_produces_zero_chunks():
    assert chunk_text("", chunk_size=10, overlap=2) == []


def test_whitespace_only_produces_zero_chunks():
    assert chunk_text("   \n\t  ", chunk_size=10, overlap=2) == []


# ── Side effects ────────────────────────────────────────────────────────

def test_chunking_is_pure_and_deterministic():
    text = "a b c d e f g h i j k l m n o"
    assert chunk_text(text, chunk_size=4, overlap=1) == chunk_text(text, chunk_size=4, overlap=1)
