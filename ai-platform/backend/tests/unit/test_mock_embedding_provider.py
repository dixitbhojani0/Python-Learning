from __future__ import annotations

import math

from backend.app.adapters.embedding.providers.mock_embedding_provider import DIMENSIONS, MockEmbeddingProvider


# ── Positive ──────────────────────────────────────────────────────────────

async def test_identical_text_produces_identical_vector():
    provider = MockEmbeddingProvider()
    [a] = await provider.embed(["hello world"])
    [b] = await provider.embed(["hello world"])
    assert a == b


async def test_different_text_produces_a_different_vector():
    provider = MockEmbeddingProvider()
    [a] = await provider.embed(["hello world"])
    [b] = await provider.embed(["goodbye world"])
    assert a != b


async def test_vector_has_the_declared_dimensions_and_is_unit_normalized():
    provider = MockEmbeddingProvider()
    [vector] = await provider.embed(["some text"])

    assert len(vector) == DIMENSIONS
    assert provider.get_dimensions() == DIMENSIONS
    norm = math.sqrt(sum(v * v for v in vector))
    assert abs(norm - 1.0) < 1e-9


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_empty_string_still_produces_a_valid_vector_not_a_crash():
    provider = MockEmbeddingProvider()
    [vector] = await provider.embed([""])
    assert len(vector) == DIMENSIONS


# ── Side effects ────────────────────────────────────────────────────────

async def test_batch_embedding_preserves_input_order():
    provider = MockEmbeddingProvider()
    [a, b, c] = await provider.embed(["first", "second", "third"])
    [a_again] = await provider.embed(["first"])

    assert a == a_again  # position 0 in the batch matches the standalone embedding of the same text
    assert a != b != c
