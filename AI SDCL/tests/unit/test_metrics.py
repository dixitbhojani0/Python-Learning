"""
tests/unit/test_metrics.py
Unit tests for backend/core/metrics.py — evaluation layer.
No Docker, no real LLM. LLM calls are mocked.
"""
import pytest
from unittest.mock import AsyncMock, patch
from backend.core.metrics import retrieval_precision, answer_relevancy


# ── retrieval_precision ────────────────────────────────────────────────────────

def test_retrieval_precision_all_keywords_found():
    chunks = [{"text": "sprint velocity is 34 story points"}]
    score = retrieval_precision(chunks, ["sprint", "velocity", "34"])
    assert score == 1.0


def test_retrieval_precision_no_keywords_found():
    chunks = [{"text": "sprint velocity is 34 story points"}]
    score = retrieval_precision(chunks, ["nginx", "oauth", "timeout"])
    assert score == 0.0


def test_retrieval_precision_partial_match():
    chunks = [{"text": "sprint velocity is 34"}]
    score = retrieval_precision(chunks, ["sprint", "nginx"])
    assert 0.0 < score < 1.0


def test_retrieval_precision_empty_chunks():
    score = retrieval_precision([], ["sprint"])
    assert score == 0.0


def test_retrieval_precision_empty_keywords():
    chunks = [{"text": "sprint velocity is 34"}]
    score = retrieval_precision(chunks, [])
    assert score == 0.0


def test_retrieval_precision_case_insensitive():
    chunks = [{"text": "Sprint Velocity Is 34 Story Points"}]
    score = retrieval_precision(chunks, ["sprint", "velocity"])
    assert score == 1.0


# ── answer_relevancy ──────────────────────────────────────────────────────────

def test_answer_relevancy_on_topic_response():
    score = answer_relevancy(
        "What is the sprint velocity?",
        "Sprint velocity is 34 story points for sprint 12.",
    )
    assert score > 0.5


def test_answer_relevancy_off_topic_response():
    score = answer_relevancy(
        "What is the sprint velocity?",
        "The sky is blue and the weather is nice today.",
    )
    assert score < 0.5


def test_answer_relevancy_empty_response():
    score = answer_relevancy("What is the sprint velocity?", "")
    assert score == 0.0


def test_answer_relevancy_returns_float():
    score = answer_relevancy("test query", "test answer")
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
