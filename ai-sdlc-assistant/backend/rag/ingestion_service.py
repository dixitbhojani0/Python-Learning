"""
backend/rag/ingestion_service.py

Ingestion business logic shared by the admin routes.

The routes in api/routes/admin/router.py stay thin HTTP handlers (validation,
rate limits, response models); everything that actually fetches, chunks, and
stores content lives here.

Every RAGPipeline call is CPU-bound (model.encode) and may time.sleep between
LLM batches, so all sync pipeline work is pushed off the event loop with
asyncio.to_thread — the API stays responsive during a long ingest.
"""
import asyncio
import base64
import logging
import os
import tempfile
import time
from pathlib import Path

from backend.core.settings import settings

logger = logging.getLogger(__name__)

# <project_root>/data — same tree the docker-compose volume mounts.
_DATA_ROOT = Path(__file__).parents[2] / "data"

# Local data/ directories ingested by default (no directory override).
_DEFAULT_LOCAL_JOBS = [
    ("sprint_docs",      "local_sprint_docs",      "doc"),
    ("adr_documents",    "local_adr",              "adr"),
    ("mock_slack",       "local_slack_mock",       "chat"),
    ("incidents",        "local_incidents",        "doc"),
    ("release_notes",    "local_release_notes",    "doc"),
    ("version_policies", "local_version_policy",   "doc"),
    ("coding_standards", "local_coding_standards", "doc"),
]


def _mark_stale(pipeline, project: str, source: str) -> None:
    """Best-effort stale-marking — first-boot ingest has nothing to mark."""
    try:
        pipeline.vector_store.mark_stale(project=project, source=source)
    except Exception:
        pass


def _invalidate_bm25(project: str) -> None:
    """
    Drop the in-memory BM25 corpus index so the next retrieve() rebuilds it
    from the updated Qdrant corpus. Must run after EVERY ingest path —
    otherwise BM25 keeps serving the pre-ingest corpus until restart.
    """
    try:
        from backend.orchestrator.nodes import get_retriever
        get_retriever().clear_bm25_cache(project)
    except Exception:
        pass  # retriever may not be initialized yet — first-boot ingest


def _new_pipeline(use_llm: bool):
    from backend.rag.pipeline import RAGPipeline
    return RAGPipeline(use_llm_context=use_llm)


# ── Local data/ directories ───────────────────────────────────────────────────

async def ingest_local(project: str, directory: str, use_llm: bool) -> tuple[int, float]:
    """Ingest local data/ dirs. Returns (chunks_ingested, duration_seconds)."""
    pipeline = _new_pipeline(use_llm)
    start = time.monotonic()
    total = 0

    if directory:
        jobs = [(directory, directory, "doc")]
    else:
        jobs = _DEFAULT_LOCAL_JOBS

    for dir_name, source, doc_type in jobs:
        d = _DATA_ROOT / dir_name
        if not d.exists():
            logger.debug("ingestion: skipping missing dir %s", d)
            continue
        _mark_stale(pipeline, project, source)
        meta = {"project": project, "source": source, "type": doc_type}
        total += await asyncio.to_thread(pipeline.ingest_directory, d, meta)

    _invalidate_bm25(project)
    return total, time.monotonic() - start


# ── Confluence space ──────────────────────────────────────────────────────────

def _is_system_page(title: str) -> bool:
    t = title.lower().strip()
    return t in ("home", "overview") or t.endswith(" home") or t.startswith("welcome to")


async def ingest_confluence(space_key: str, project: str, use_llm: bool) -> tuple[int, int, float]:
    """
    Ingest all pages (body text + PDF attachments) from a Confluence space
    via the sdlc-mcp-server. Returns (chunks_ingested, pages_fetched, duration).
    """
    from backend.mcp_client.client import as_list, call_mcp_tool

    start = time.monotonic()
    pages = as_list(await call_mcp_tool("confluence_get_all_page_texts", {"space_key": space_key}))
    if not pages:
        return 0, 0, 0.0

    pipeline = _new_pipeline(use_llm)
    source = f"confluence_{space_key.lower()}"
    _mark_stale(pipeline, project, source)

    total = 0
    meta = {"project": project, "source": source, "type": "doc"}
    all_page_meta = as_list(await call_mcp_tool("confluence_get_pages", {"space_key": space_key}))

    # Phase 1: body text from pages that have content
    for page in pages:
        count = await asyncio.to_thread(
            pipeline._ingest_text,
            page["content"],
            page["title"],
            "doc",
            {**meta, "doc_title": page["title"], "url": page.get("url", "")},
        )
        total += count
        logger.debug("ingestion/confluence: '%s' (text) → %d chunks", page["title"], count)

    # Phase 2: PDF attachments (system-page filtering is done server-side inside
    # confluence_get_all_page_texts; get_pages returns all, so skip them here)
    for page_info in all_page_meta:
        if _is_system_page(page_info["title"]):
            continue
        try:
            attachments = as_list(
                await call_mcp_tool("confluence_get_page_attachments", {"page_id": page_info["id"]})
            )
            for att in attachments:
                b64 = await call_mcp_tool("confluence_download_attachment", {"download_url": att["download_url"]})
                if not b64:
                    continue
                pdf_bytes = base64.b64decode(b64)
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                try:
                    att_title = att["title"].replace(".pdf", "").replace("_", " ").replace("-", " ")
                    att_count = await asyncio.to_thread(
                        pipeline.ingest_file,
                        tmp_path,
                        {**meta, "doc_title": att_title, "url": att["download_url"]},
                    )
                    total += att_count
                    logger.info("ingestion/confluence: '%s' (PDF) → %d chunks", att["title"], att_count)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        except Exception:
            logger.exception(
                "ingestion/confluence: attachment processing failed for page '%s'",
                page_info["title"],
            )

    _invalidate_bm25(project)
    return total, len(all_page_meta), time.monotonic() - start


# ── Jira tickets ──────────────────────────────────────────────────────────────

async def ingest_jira(project: str, max_tickets: int) -> tuple[int, int, float]:
    """
    Ingest open + recently resolved Jira tickets as type='ticket' chunks.
    Returns (chunks_ingested, tickets_fetched, duration_seconds).
    """
    from backend.mcp_client.client import as_list, call_mcp_tool

    start = time.monotonic()
    sprint_board, blocked_tickets, all_tickets = await asyncio.gather(
        call_mcp_tool("jira_get_sprint_board", {"project": project}),
        call_mcp_tool("jira_get_blocked_tickets", {"project": project}),
        call_mcp_tool("jira_search_tickets", {"query": "", "project": project}),
    )
    blocked_tickets = as_list(blocked_tickets)
    all_tickets = as_list(all_tickets)

    seen: set[str] = set()
    tickets: list[dict] = []
    for t in [*all_tickets, *blocked_tickets]:
        if t.get("id") and t["id"] not in seen:
            seen.add(t["id"])
            tickets.append(t)
    tickets = tickets[:max_tickets]

    if not tickets:
        return 0, 0, 0.0

    pipeline = _new_pipeline(False)  # ticket text is already structured — no LLM prefix needed
    source = "jira_tickets"
    _mark_stale(pipeline, project, source)

    total = 0
    for ticket in tickets:
        text = (
            f"Ticket {ticket.get('id', '')}: {ticket.get('title', '')}\n"
            f"Status: {ticket.get('status', '')}\n"
            f"Priority: {ticket.get('priority', '')}\n"
            f"Assignee: {ticket.get('assignee', 'unassigned')}\n"
            f"Labels: {', '.join(ticket.get('labels', []))}\n"
            f"Blockers: {', '.join(ticket.get('blockers', []))}\n"
            f"Sprint: {ticket.get('sprint', '')}\n"
            f"Description: {ticket.get('description', '')}\n"
            f"Created: {ticket.get('created', '')} Updated: {ticket.get('updated', '')}"
        )
        doc_title = f"{ticket.get('id', '')} — {ticket.get('title', '')[:80]}"
        total += await asyncio.to_thread(
            pipeline._ingest_text,
            text,
            doc_title,
            "ticket",
            {
                "project":   project,
                "source":    source,
                "type":      "ticket",
                "doc_title": doc_title,
                "url":       ticket.get("url", ""),
            },
        )

    _invalidate_bm25(project)
    return total, len(tickets), time.monotonic() - start


# ── Cross-document links ──────────────────────────────────────────────────────

async def build_links(project: str, min_similarity: float) -> tuple[int, float]:
    """Build cross-document similarity links. Returns (chunks_linked, duration)."""
    pipeline = _new_pipeline(False)
    start = time.monotonic()
    linked = await asyncio.to_thread(pipeline.build_cross_document_links, project, min_similarity)
    return linked, time.monotonic() - start
