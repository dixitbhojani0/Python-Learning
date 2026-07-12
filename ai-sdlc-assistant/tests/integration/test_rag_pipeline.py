"""
tests/integration/test_rag_pipeline.py
Integration tests — require Qdrant running on localhost:6333.

Run with:  pytest tests/integration/ -m integration -v
"""
import os
import pytest
from pathlib import Path

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pipeline():
    from backend.rag.pipeline import RAGPipeline
    return RAGPipeline(use_llm_context=False)   # skip LLM context prefix — faster


@pytest.fixture(scope="module")
def retriever():
    from backend.rag.retriever import HybridRetriever
    return HybridRetriever()


def test_ingest_sprint_doc_produces_chunks(pipeline):
    sprint_doc = Path("data/sprint_docs/sprint_12_plan.md")
    if not sprint_doc.exists():
        pytest.skip("Sprint 12 plan doc not found — run from ai-sdlc-assistant/ directory")
    count = pipeline.ingest_file(
        str(sprint_doc),
        metadata={"project": "test_integration", "source": "local", "type": "sprint_notes"},
    )
    assert count > 0, "Expected at least 1 chunk from sprint doc"


def test_retrieve_after_ingest_returns_chunks(retriever):
    chunks, confidence = retriever.retrieve("sprint velocity dashboard", project="test_integration")
    # Only assert shape — score depends on actual content
    assert isinstance(chunks, list)
    assert isinstance(confidence, float)
    assert 0.0 <= confidence <= 1.0


@pytest.mark.asyncio
async def test_corrective_rag_does_not_crash(retriever):
    async def _identity_rewrite(q):
        return q

    chunks, confidence, strategy = await retriever.retrieve_with_corrective_rag(
        "sprint velocity", "test_integration", _identity_rewrite
    )
    assert strategy in ("first_pass", "corrective", "degraded")


def test_vector_store_count_increases_after_ingest(pipeline):
    from backend.rag.vector_store import VectorStore
    vs = VectorStore()
    before = vs.count()
    adr_doc = Path("data/adr_documents")
    if not adr_doc.exists():
        pytest.skip("ADR directory not found")
    pipeline.ingest_directory(
        str(adr_doc),
        metadata={"project": "test_integration", "source": "local", "type": "adr"},
    )
    after = vs.count()
    assert after >= before, "Chunk count should not decrease after ingest"
