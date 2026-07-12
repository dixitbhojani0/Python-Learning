"""
tests/unit/test_retriever.py
Unit tests for backend/rag/retriever.py — Qdrant and embed model are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock
from backend.rag.retriever import _sigmoid, _reciprocal_rank_fusion, HybridRetriever


# ── Pure utility functions ─────────────────────────────────────────────────────

def test_sigmoid_returns_zero_point_five_for_zero():
    assert abs(_sigmoid(0.0) - 0.5) < 0.01


def test_sigmoid_increases_monotonically():
    scores = [-5, -2, 0, 2, 5]
    sigmas = [_sigmoid(s) for s in scores]
    assert sigmas == sorted(sigmas)


def test_sigmoid_handles_extreme_positive():
    result = _sigmoid(1000)
    assert result >= 0.9   # large positive → near 1.0


def test_sigmoid_handles_extreme_negative():
    result = _sigmoid(-1000)
    assert result <= 0.1   # large negative → near 0.0


def test_rrf_merges_two_ranked_lists():
    vector_results = [
        {"id": "a", "text": "alpha"},
        {"id": "b", "text": "beta"},
        {"id": "c", "text": "gamma"},
    ]
    # BM25 ranks b highest — RRF should surface b near the top
    bm25_results = [
        {"id": "b", "text": "beta"},
        {"id": "a", "text": "alpha"},
        {"id": "c", "text": "gamma"},
    ]
    merged = _reciprocal_rank_fusion(vector_results, bm25_results)
    ids = [d["id"] for d in merged]
    assert "b" in ids[:2]


def test_rrf_returns_same_length_as_input():
    docs = [{"id": str(i), "text": f"doc {i}"} for i in range(5)]
    result = _reciprocal_rank_fusion(docs, docs)
    assert len(result) == 5


# ── HybridRetriever with mocked internals ─────────────────────────────────────

@pytest.fixture
def retriever(mock_vector_store):
    with (
        patch("backend.rag.retriever.embed_text") as mock_embed,
        patch("backend.rag.retriever._get_rerank_model") as mock_rerank_fn,
    ):
        # Return a proper numpy array so .tolist() works
        mock_embed.return_value = [0.1] * 384

        rerank_model = MagicMock()
        rerank_model.predict.return_value = [5.0]
        mock_rerank_fn.return_value = rerank_model

        r = HybridRetriever()
        r.vector_store = mock_vector_store
        yield r


def test_retriever_returns_chunks_and_confidence(retriever):
    chunks, confidence = retriever.retrieve("sprint velocity", project="SDLC")
    assert len(chunks) == 1
    assert 0.0 < confidence <= 1.0


def test_retriever_returns_empty_when_no_vector_results(retriever, mock_vector_store):
    mock_vector_store.search.return_value = []
    chunks, confidence = retriever.retrieve("sprint velocity", project="SDLC")
    assert chunks == []
    assert confidence == 0.0


def test_retriever_returns_empty_for_blank_query(retriever):
    chunks, confidence = retriever.retrieve("", project="SDLC")
    assert chunks == []
    assert confidence == 0.0


@pytest.mark.asyncio
async def test_corrective_rag_returns_first_pass_when_confidence_high(retriever):
    async def _no_op_rewrite(q):
        return q + " reformulated"

    with patch.object(retriever, "retrieve", return_value=([MagicMock(score=0.8)], 0.8)):
        chunks, confidence, strategy = await retriever.retrieve_with_corrective_rag(
            "sprint velocity", "SDLC", _no_op_rewrite
        )
    assert strategy == "first_pass"
    assert confidence == 0.8


@pytest.mark.asyncio
async def test_corrective_rag_retries_when_confidence_low(retriever):
    call_count = {"n": 0}

    def _low_then_high(q, p):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [], 0.1
        return [MagicMock(score=0.7)], 0.7

    async def _rewrite(q):
        return q + " retry"

    with patch.object(retriever, "retrieve", side_effect=_low_then_high):
        chunks, confidence, strategy = await retriever.retrieve_with_corrective_rag(
            "vague query", "SDLC", _rewrite
        )
    assert strategy == "corrective"
    assert call_count["n"] == 2
