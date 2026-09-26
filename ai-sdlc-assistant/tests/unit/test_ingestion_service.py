"""
tests/unit/test_ingestion_service.py

Unit tests for backend/rag/ingestion_service.py — the shared ingestion logic
extracted from the admin routes. Verifies the Confluence path passes the
contextual-prefix flag through, tags chunks with the right metadata, and
invalidates the BM25 cache after every ingest.
"""
from unittest.mock import MagicMock

import pytest

from backend.rag import ingestion_service


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Replace RAGPipeline with a recorder; capture the use_llm flag."""
    pipeline = MagicMock()
    pipeline._ingest_text.return_value = 2
    pipeline.ingest_file.return_value = 3
    pipeline.ingest_directory.return_value = 5

    created_with: dict = {}

    def _new_pipeline(use_llm: bool):
        created_with["use_llm"] = use_llm
        return pipeline

    monkeypatch.setattr(ingestion_service, "_new_pipeline", _new_pipeline)
    pipeline.created_with = created_with
    return pipeline


@pytest.fixture
def bm25_calls(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ingestion_service, "_invalidate_bm25", calls.append)
    return calls


def _fake_mcp(monkeypatch, responses: dict):
    async def call_mcp_tool(tool: str, args: dict):
        return responses.get(tool, [])
    monkeypatch.setattr("backend.mcp_client.client.call_mcp_tool", call_mcp_tool)


async def test_confluence_ingest_passes_llm_flag_and_metadata(fake_pipeline, bm25_calls, monkeypatch):
    _fake_mcp(monkeypatch, {
        "confluence_get_all_page_texts": [
            {"title": "Sprint Notes", "content": "some content", "url": "http://x/wiki/1"},
        ],
        "confluence_get_pages": [{"id": "1", "title": "Sprint Notes"}],
        "confluence_get_page_attachments": [],
    })

    total, pages, duration = await ingestion_service.ingest_confluence(
        space_key="SDLC", project="SDLC", use_llm=True,
    )

    assert total == 2 and pages == 1
    # The contextual-prefix flag must reach the pipeline (used to be hardcoded False).
    assert fake_pipeline.created_with["use_llm"] is True
    # Chunk metadata contract: project/source/type/doc_title/url.
    _, args, kwargs = fake_pipeline._ingest_text.mock_calls[0]
    meta = args[3] if len(args) > 3 else kwargs["metadata"]
    assert meta["project"] == "SDLC"
    assert meta["source"] == "confluence_sdlc"
    assert meta["type"] == "doc"
    assert meta["doc_title"] == "Sprint Notes"
    # BM25 cache must be dropped after the ingest.
    assert bm25_calls == ["SDLC"]


async def test_confluence_ingest_empty_space(fake_pipeline, bm25_calls, monkeypatch):
    _fake_mcp(monkeypatch, {"confluence_get_all_page_texts": []})
    total, pages, duration = await ingestion_service.ingest_confluence("SDLC", "SDLC", use_llm=True)
    assert (total, pages, duration) == (0, 0, 0.0)
    assert bm25_calls == []  # nothing ingested, nothing to invalidate


async def test_jira_ingest_dedups_and_invalidates_bm25(fake_pipeline, bm25_calls, monkeypatch):
    ticket = {"id": "SDLC-1", "title": "Fix login", "status": "OPEN"}
    _fake_mcp(monkeypatch, {
        "jira_get_sprint_board": {},
        "jira_get_blocked_tickets": [ticket],           # duplicate of the search hit
        "jira_search_tickets": [ticket, {"id": "SDLC-2", "title": "Add tests"}],
    })

    total, fetched, _ = await ingestion_service.ingest_jira(project="SDLC", max_tickets=10)

    assert fetched == 2                       # SDLC-1 deduped across the two lists
    assert total == 2 * 2                     # 2 tickets × 2 chunks each
    assert fake_pipeline.created_with["use_llm"] is False  # tickets skip the LLM prefix
    assert bm25_calls == ["SDLC"]
