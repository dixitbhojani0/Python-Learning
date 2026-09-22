"""
Pure-function tests for the chat<->retrieval wiring — no DB, no HTTP, just
plain in-memory model instances, since Chunk/Document are ordinary SQLAlchemy
objects that don't require a session to construct (only to persist).
"""
from __future__ import annotations

import uuid

from backend.app.db.models import Chunk
from backend.app.rag.chat_augmentation import build_rag_augmentation
from backend.app.rag.retrieval import ScoredChunk


def _scored(content: str, distance: float = 0.1) -> ScoredChunk:
    chunk = Chunk(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        embedding=[0.0] * 768,
    )
    return ScoredChunk(chunk=chunk, distance=distance)


# ── Positive ──────────────────────────────────────────────────────────────

def test_single_result_produces_a_system_prompt_containing_its_content():
    result = _scored("the wifi password is sunflower123")
    augmentation = build_rag_augmentation([result])

    assert "sunflower123" in augmentation.system_prompt
    assert augmentation.citations == [
        {"chunk_id": str(result.chunk.id), "document_id": str(result.chunk.document_id)}
    ]


def test_multiple_results_are_all_included_in_the_context_block():
    a, b = _scored("fact one"), _scored("fact two")
    augmentation = build_rag_augmentation([a, b])

    assert "fact one" in augmentation.system_prompt
    assert "fact two" in augmentation.system_prompt
    assert len(augmentation.citations) == 2


# ── Negative / edge ─────────────────────────────────────────────────────

def test_empty_results_produce_no_augmentation_at_all():
    augmentation = build_rag_augmentation([])

    assert augmentation.system_prompt == ""
    assert augmentation.citations == []


# ── Side effects ────────────────────────────────────────────────────────

def test_citation_order_matches_input_order_not_reordered():
    a, b, c = _scored("first"), _scored("second"), _scored("third")
    augmentation = build_rag_augmentation([a, b, c])

    assert [cite["chunk_id"] for cite in augmentation.citations] == [
        str(a.chunk.id),
        str(b.chunk.id),
        str(c.chunk.id),
    ]


def test_citations_carry_string_uuids_not_uuid_objects():
    # This list gets json.dumps()'d directly in chat_routes.py — a raw
    # uuid.UUID would raise TypeError at serialization time, not here, so
    # this guards against that regression before it ever reaches an SSE event.
    result = _scored("x")
    augmentation = build_rag_augmentation([result])

    assert all(isinstance(v, str) for cite in augmentation.citations for v in cite.values())
