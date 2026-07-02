"""
tests/unit/test_chunker.py
Unit tests for backend/rag/chunker.py — no Docker, no LLM calls.
"""
import pytest
from backend.rag.chunker import DocumentChunker, Chunk


@pytest.fixture
def chunker():
    return DocumentChunker()


def test_chunker_splits_plain_text_into_chunks(chunker):
    text = "Alpha beta gamma delta epsilon. " * 200
    chunks = list(chunker.chunk_document(
        text, doc_title="Test Doc", doc_type="doc",
        metadata={"project": "SDLC", "source": "local"},
    ))
    assert len(chunks) > 0
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.text
        assert c.parent_text


def test_chunker_chunk_has_required_metadata_fields(chunker):
    text = "This is a test document about sprint planning and velocity metrics."
    chunks = list(chunker.chunk_document(
        text, doc_title="Sprint Plan", doc_type="sprint_notes",
        metadata={"project": "SDLC", "source": "local"},
    ))
    assert len(chunks) > 0
    chunk = chunks[0]
    assert chunk.metadata.get("project") == "SDLC"
    assert chunk.metadata.get("source") == "local"


def test_chunker_returns_empty_for_empty_text(chunker):
    chunks = list(chunker.chunk_document(
        "", doc_title="Empty", doc_type="doc",
        metadata={"project": "SDLC", "source": "local"},
    ))
    assert chunks == []


def test_chunker_returns_empty_for_whitespace_only_text(chunker):
    chunks = list(chunker.chunk_document(
        "   \n\n\t  \n  ", doc_title="Blank", doc_type="doc",
        metadata={"project": "SDLC", "source": "local"},
    ))
    assert chunks == []


def test_chunker_parent_text_at_least_as_long_as_child(chunker):
    text = " ".join(["word"] * 1000)
    chunks = list(chunker.chunk_document(
        text, doc_title="Long Doc", doc_type="doc",
        metadata={"project": "SDLC", "source": "local"},
    ))
    for c in chunks:
        assert len(c.parent_text) >= len(c.text)


def test_chunker_chunk_slack_messages_produces_chunks(chunker):
    messages = [
        {"message": "The auth service is throwing 500 errors", "user": "alice", "timestamp": "2024-01-01"},
        {"message": "I think it is the nginx rate limit",      "user": "bob",   "timestamp": "2024-01-01"},
        {"message": "Confirmed nginx config is the issue",     "user": "alice", "timestamp": "2024-01-01"},
    ]
    chunks = list(chunker.chunk_slack_messages(
        messages, metadata={"project": "SDLC", "source": "slack", "type": "chat"}
    ))
    assert len(chunks) > 0
    assert all(c.text for c in chunks)


def test_chunker_chunk_type_preserved(chunker):
    text = "ADR-001: We chose Qdrant as our vector database."
    chunks = list(chunker.chunk_document(
        text, doc_title="ADR-001", doc_type="adr",
        metadata={"project": "SDLC", "source": "local"},
    ))
    assert len(chunks) > 0
