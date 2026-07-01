"""
backend/api/routes/admin/router.py

Admin REST API router — all 15 endpoint handlers.

Endpoints:
  GET  /admin/stats               — System health: Qdrant, Redis, session counts
  GET  /admin/chunks              — Browse Qdrant chunks (paginated, filterable)
  POST /admin/ingest              — Trigger RAG re-ingestion from local data/
  GET  /admin/sessions            — List recent session turns from SQLite
  GET  /admin/config/{key}        — Return one YAML config section as JSON
  POST /admin/config/reload       — Force hot-reload of all YAML config files
  POST /admin/ingest/confluence   — Ingest pages from Confluence space
  GET  /admin/memory              — List semantic memory facts
  DELETE /admin/memory            — Clear semantic memory facts for a project
  POST /admin/clear               — Full data reset for a project
  POST /admin/ingest/jira         — Ingest Jira tickets into RAG
  POST /admin/build-links         — Build cross-document similarity links
  POST /admin/test-slack          — Trigger a test Slack notification

Design rule: these routes do real work (Qdrant queries, ingest, DB reads).
They do NOT call the LangGraph graph or any agent — admin is infrastructure,
not conversation.
"""
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.limiter import limiter
from backend.api.routes.admin.helpers import (
    qdrant_chunk_count,
    qdrant_collection_name,
    redis_key_count,
    session_turn_count,
    semantic_fact_count,
)
from backend.api.routes.admin.models import (
    ConfluenceIngestRequest,
    ConfluenceIngestResponse,
    CrossDocLinksResponse,
    IngestRequest,
    IngestResponse,
    JiraIngestRequest,
    JiraIngestResponse,
    StatsResponse,
)
from backend.auth.middleware import UserContext, get_admin_user
from backend.core.config_loader import config
from backend.core.settings import settings
from backend.memory.session_store import session_store

logger = logging.getLogger(__name__)
router = APIRouter()

# ── MCP outbound-host sub-router (admin manages connections to other MCP servers)
from backend.api.routes.admin.mcp_servers import router as _mcp_servers_router  # noqa: E402
router.include_router(_mcp_servers_router)


# ── Stats ──────────────────────────────────────────────────────────────────────

@limiter.limit("30/minute")
@router.get("/admin/stats", response_model=StatsResponse)
async def admin_stats(
    request: Request,
    user: UserContext = Depends(get_admin_user),
):
    """
    System health summary for the admin dashboard home page.
    Returns chunk counts, Redis key count, session turn count, and app config.
    """
    logger.info("admin/stats: requested by %s", user.name)
    return StatsResponse(
        qdrant_chunks=qdrant_chunk_count(),
        qdrant_collection=qdrant_collection_name(),
        redis_keys=redis_key_count(),
        session_turns=session_turn_count(),
        semantic_facts=semantic_fact_count(),
        app_env=settings.APP_ENV,
        default_project=settings.DEFAULT_PROJECT,
    )


# ── Chunks Browser ────────────────────────────────────────────────────────────

@limiter.limit("20/minute")
@router.get("/admin/chunks")
async def admin_chunks(
    request:  Request,
    project:  str   = Query(default=settings.DEFAULT_PROJECT, description="Filter by project tag"),
    doc_type: str   = Query(default="",       description="Filter by type: doc|adr|chat|ticket"),
    limit:    int   = Query(default=50,        ge=1, le=500),
    offset:   int   = Query(default=0,         ge=0),
    user: UserContext = Depends(get_admin_user),
):
    """
    Browse Qdrant chunks with optional project and doc_type filters.
    Used by the Streamlit RAG Manager page to inspect stored knowledge.

    Returns a list of chunk payloads (metadata only — not the embedding vector).
    """
    logger.info(
        "admin/chunks: project='%s' type='%s' limit=%d offset=%d user='%s'",
        project, doc_type, limit, offset, user.name,
    )
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        client     = QdrantClient(url=settings.QDRANT_URL)
        collection = qdrant_collection_name()

        must = [FieldCondition(key="project", match=MatchValue(value=project))]
        if doc_type:
            must.append(FieldCondition(key="type", match=MatchValue(value=doc_type)))

        results, _next = client.scroll(
            collection_name=collection,
            scroll_filter=Filter(must=must),
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        chunks = []
        for point in results:
            payload = point.payload or {}
            chunks.append({
                "id":           str(point.id),
                "project":      payload.get("project", ""),
                "type":         payload.get("type", ""),
                "source":       payload.get("source", ""),
                "doc_title":    payload.get("doc_title", ""),
                "stale":        payload.get("stale", False),
                "text_preview": (payload.get("text") or "")[:200],
                "has_parent":   bool(payload.get("parent_text")),
                "has_context":  bool(payload.get("context_prefix")),
            })

        return {"chunks": chunks, "count": len(chunks), "offset": offset}

    except Exception:
        logger.exception("admin/chunks: failed")
        raise HTTPException(status_code=503, detail="Could not query Qdrant. Is it running?")


# ── Ingest — Local Files ──────────────────────────────────────────────────────

@limiter.limit("5/minute")
@router.post("/admin/ingest", response_model=IngestResponse)
async def admin_ingest(
    request: Request,
    body:    IngestRequest,
    user: UserContext = Depends(get_admin_user),
):
    """
    Trigger RAG re-ingestion from local data/ directories.

    Steps:
      1. Mark existing chunks for the project+source as stale
      2. Run the RAGPipeline over the specified (or all) data directories
      3. Return chunk count and wall-clock duration

    use_llm=False (default) skips Groq API calls — ingests in seconds.
    use_llm=True  uses contextual prefixes  — slower but higher RAG quality.
    """
    logger.info(
        "admin/ingest: project='%s' use_llm=%s user='%s'",
        body.project, body.use_llm, user.name,
    )

    if body.use_llm and "placeholder" in settings.GROQ_API_KEY:
        raise HTTPException(
            status_code=422,
            detail="use_llm=true requires GROQ_API_KEY to be set in .env. Use use_llm=false for demo mode.",
        )

    try:
        from backend.rag.pipeline import RAGPipeline

        pipeline  = RAGPipeline(use_llm_context=body.use_llm)
        start     = time.monotonic()
        total     = 0
        data_root = Path(__file__).parents[4] / "data"

        if body.directory:
            ingest_jobs = [
                {
                    "dir":  data_root / body.directory,
                    "meta": {"project": body.project, "source": body.directory, "type": "doc"},
                }
            ]
        else:
            ingest_jobs = [
                {"dir": data_root / "sprint_docs",      "meta": {"project": body.project, "source": "local_sprint_docs",      "type": "doc"}},
                {"dir": data_root / "adr_documents",    "meta": {"project": body.project, "source": "local_adr",              "type": "adr"}},
                {"dir": data_root / "mock_slack",       "meta": {"project": body.project, "source": "local_slack_mock",       "type": "chat"}},
                {"dir": data_root / "incidents",        "meta": {"project": body.project, "source": "local_incidents",        "type": "doc"}},
                {"dir": data_root / "release_notes",    "meta": {"project": body.project, "source": "local_release_notes",   "type": "doc"}},
                {"dir": data_root / "version_policies", "meta": {"project": body.project, "source": "local_version_policy",  "type": "doc"}},
                {"dir": data_root / "coding_standards", "meta": {"project": body.project, "source": "local_coding_standards", "type": "doc"}},
            ]

        for job in ingest_jobs:
            d = job["dir"]
            if not d.exists():
                logger.debug("admin/ingest: skipping missing dir %s", d)
                continue
            try:
                pipeline.vector_store.mark_stale(project=body.project, source=job["meta"]["source"])
            except Exception:
                pass
            total += pipeline.ingest_directory(d, job["meta"])

        duration = time.monotonic() - start

        # Invalidate the in-memory BM25 corpus index so the next retrieve()
        # call rebuilds from the updated Qdrant corpus.
        try:
            from backend.orchestrator.nodes import get_retriever
            get_retriever().clear_bm25_cache(body.project)
        except Exception:
            pass  # retriever may not be initialized yet — first-boot ingest

        msg = (
            f"Ingested {total} chunks from project='{body.project}' "
            f"in {duration:.1f}s (LLM context: {'enabled' if body.use_llm else 'disabled'})."
        )
        logger.info("admin/ingest: %s", msg)
        return IngestResponse(chunks_ingested=total, duration_seconds=round(duration, 2), message=msg)

    except Exception:
        logger.exception("admin/ingest: pipeline failed")
        raise HTTPException(status_code=500, detail="Ingestion failed. Check server logs.")


# ── Sessions ──────────────────────────────────────────────────────────────────

@limiter.limit("20/minute")
@router.get("/admin/sessions")
async def admin_sessions(
    request:   Request,
    project:   str = Query(default=settings.DEFAULT_PROJECT),
    limit:     int = Query(default=20, ge=1, le=100),
    user: UserContext = Depends(get_admin_user),
):
    """Return the most recent conversation turns for a project from SQLite."""
    logger.info("admin/sessions: project='%s' limit=%d user='%s'", project, limit, user.name)
    turns = await session_store.aload_project_turns(
        project_id=project,
        limit=limit,
        response_truncate=300,
    )
    return {"turns": turns, "count": len(turns)}


# ── Config ────────────────────────────────────────────────────────────────────

@limiter.limit("30/minute")
@router.get("/admin/config/{key}")
async def admin_config(
    request: Request,
    key:     Literal["prompts", "agents", "llm", "mcp_registry", "rag_sources", "redis", "chunking"],
    user: UserContext = Depends(get_admin_user),
):
    """
    Return one YAML config section as JSON.
    Valid keys: prompts | agents | llm | mcp_registry | rag_sources | redis | chunking
    """
    dispatch = {
        "prompts":      lambda: config._configs.get("prompts", {}),
        "agents":       lambda: config._configs.get("agents", {}),
        "llm":          lambda: config._configs.get("llm", {}),
        "mcp_registry": lambda: config._configs.get("mcp_registry", {}),
        "rag_sources":  lambda: config._configs.get("rag_sources", {}),
        "redis":        lambda: config._configs.get("redis", {}),
        "chunking":     lambda: config._configs.get("chunking", {}),
    }
    fn = dispatch.get(key)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"Config key '{key}' not found.")
    return {"key": key, "config": fn()}


@limiter.limit("5/minute")
@router.post("/admin/config/reload")
async def admin_config_reload(
    request: Request,
    user: UserContext = Depends(get_admin_user),
):
    """Force a hot-reload of all YAML config files."""
    logger.info("admin/config/reload: triggered by %s", user.name)
    try:
        config._load_all()
        return {"status": "reloaded", "message": "All YAML config files reloaded successfully."}
    except Exception:
        logger.exception("admin/config/reload: failed")
        raise HTTPException(status_code=500, detail="Config reload failed. Check server logs.")


# ── Ingest — Confluence ───────────────────────────────────────────────────────

@limiter.limit("5/minute")
@router.post("/admin/ingest/confluence", response_model=ConfluenceIngestResponse)
async def admin_ingest_confluence(
    request: Request,
    body:    ConfluenceIngestRequest,
    user: UserContext = Depends(get_admin_user),
):
    """
    Fetch all pages from a Confluence space and ingest them into Qdrant.

    Uses the same JIRA_EMAIL + JIRA_TOKEN credentials — no extra setup needed.
    Falls back to mock pages when credentials are placeholders (dev mode).
    """
    logger.info(
        "admin/ingest/confluence: space='%s' project='%s' user='%s'",
        body.space_key, body.project, user.name,
    )
    try:
        from backend.mcp.connectors.confluence_connector import ConfluenceConnector, _is_system_page
        from backend.rag.pipeline import RAGPipeline

        connector = ConfluenceConnector(name="confluence", connector_config={})
        if not connector.is_available():
            raise HTTPException(
                status_code=400,
                detail="Confluence credentials are not configured. Please set Confluence credentials in .env to enable Confluence ingestion."
            )

        start = time.monotonic()
        pages = await connector.get_all_page_texts(body.space_key)

        if not pages:
            return ConfluenceIngestResponse(
                chunks_ingested=0,
                pages_fetched=0,
                duration_seconds=0.0,
                message=f"No pages found in Confluence space '{body.space_key}'.",
            )

        pipeline = RAGPipeline(use_llm_context=False)
        source   = f"confluence_{body.space_key.lower()}"

        try:
            pipeline.vector_store.mark_stale(project=body.project, source=source)
        except Exception:
            pass

        total         = 0
        meta          = {"project": body.project, "source": source, "type": "doc"}
        all_page_meta = await connector.get_pages(body.space_key)

        # Phase 1: Ingest body text from pages that have content
        for page in pages:
            count = pipeline._ingest_text(
                text=page["content"],
                doc_title=page["title"],
                doc_type="doc",
                metadata={**meta, "doc_title": page["title"], "url": page.get("url", "")},
            )
            total += count
            logger.debug("admin/ingest/confluence: '%s' (text) → %d chunks", page["title"], count)

        # Phase 2: Check all non-system pages for PDF attachments
        for page_info in all_page_meta:
            if _is_system_page(page_info["title"]):
                continue
            try:
                attachments = await connector.get_page_attachments(page_info["id"])
                for att in attachments:
                    pdf_bytes = await connector.download_attachment_bytes(att["download_url"])
                    if not pdf_bytes:
                        continue
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        tmp.write(pdf_bytes)
                        tmp_path = tmp.name
                    try:
                        att_title = att["title"].replace(".pdf", "").replace("_", " ").replace("-", " ")
                        att_meta  = {**meta, "doc_title": att_title, "url": att["download_url"]}
                        att_count = pipeline.ingest_file(tmp_path, att_meta)
                        total += att_count
                        logger.info(
                            "admin/ingest/confluence: '%s' (PDF attachment) → %d chunks",
                            att["title"], att_count,
                        )
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
            except Exception:
                logger.exception(
                    "admin/ingest/confluence: attachment processing failed for page '%s'",
                    page_info["title"],
                )

        duration = time.monotonic() - start
        msg = (
            f"Ingested {total} chunks from {len(all_page_meta)} Confluence pages "
            f"(space='{body.space_key}') in {duration:.1f}s."
        )
        logger.info("admin/ingest/confluence: %s", msg)
        return ConfluenceIngestResponse(
            chunks_ingested=total,
            pages_fetched=len(all_page_meta),
            duration_seconds=round(duration, 2),
            message=msg,
        )

    except Exception:
        logger.exception("admin/ingest/confluence: failed")
        raise HTTPException(status_code=500, detail="Confluence ingest failed. Check server logs.")


# ── Memory (Semantic Facts) ───────────────────────────────────────────────────

@limiter.limit("20/minute")
@router.get("/admin/memory")
async def admin_memory(
    request: Request,
    project: str = Query(default=settings.DEFAULT_PROJECT),
    limit:   int = Query(default=50, ge=1, le=200),
    user: UserContext = Depends(get_admin_user),
):
    """
    List extracted long-term semantic facts from SemanticMemory (Qdrant).
    These are project-specific facts extracted from past conversations.
    """
    logger.info("admin/memory: project='%s' limit=%d user='%s'", project, limit, user.name)
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        client = QdrantClient(url=settings.QDRANT_URL)
        results, _ = client.scroll(
            collection_name="semantic_memory",
            scroll_filter=Filter(must=[FieldCondition(key="project_id", match=MatchValue(value=project))]),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        facts = []
        for point in results:
            p = point.payload or {}
            facts.append({
                "id":           str(point.id),
                "text":         p.get("text", ""),
                "category":     p.get("category", ""),
                "project_id":   p.get("project_id", ""),
                "created_at":   p.get("created_at", ""),
                "source_query": p.get("source_query", ""),
            })
        return {"facts": facts, "count": len(facts)}
    except Exception:
        logger.exception("admin/memory: failed")
        return {"facts": [], "count": 0, "error": "semantic_memory collection may not exist yet"}


@limiter.limit("3/minute")
@router.delete("/admin/memory")
async def admin_memory_clear(
    request: Request,
    project: str = Query(default=settings.DEFAULT_PROJECT),
    user: UserContext = Depends(get_admin_user),
):
    """Delete all semantic memory facts for a project."""
    logger.info("admin/memory/clear: project='%s' user='%s'", project, user.name)
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector

        client = QdrantClient(url=settings.QDRANT_URL)
        client.delete(
            collection_name="semantic_memory",
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="project_id", match=MatchValue(value=project))])
            ),
        )
        logger.info("admin/memory/clear: deleted facts for project='%s'", project)
        return {"status": "cleared", "project": project}
    except Exception:
        logger.exception("admin/memory/clear: failed")
        raise HTTPException(status_code=500, detail="Could not clear semantic memory.")


# ── Full Reset ────────────────────────────────────────────────────────────────

@limiter.limit("2/minute")
@router.post("/admin/clear")
async def admin_clear_all(
    request: Request,
    project: str = Query(default=settings.DEFAULT_PROJECT),
    user: UserContext = Depends(get_admin_user),
):
    """
    Fresh-start reset — clears ALL data for a project:
      - Qdrant RAG chunks
      - Semantic memory facts
      - Redis cache (all keys)
      - SQLite session turns for this project
    """
    import sqlite3

    logger.warning("admin/clear: FULL RESET triggered by %s for project='%s'", user.name, project)
    results: dict = {}

    # 1. Delete Qdrant RAG chunks for project
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
        client     = QdrantClient(url=settings.QDRANT_URL)
        collection = qdrant_collection_name()
        client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="project", match=MatchValue(value=project))])
            ),
        )
        results["qdrant_rag_chunks"] = "cleared"
    except Exception:
        logger.exception("admin/clear: Qdrant RAG clear failed")
        results["qdrant_rag_chunks"] = "error"

    # 2. Delete semantic memory facts for project
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
        client = QdrantClient(url=settings.QDRANT_URL)
        client.delete(
            collection_name="semantic_memory",
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="project_id", match=MatchValue(value=project))])
            ),
        )
        results["semantic_memory"] = "cleared"
    except Exception:
        logger.exception("admin/clear: semantic memory clear failed")
        results["semantic_memory"] = "error"

    # 3. Flush Redis (all keys)
    try:
        import redis as redis_lib
        r = redis_lib.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.flushdb()
        results["redis_cache"] = "flushed"
    except Exception:
        logger.exception("admin/clear: Redis flush failed")
        results["redis_cache"] = "error"

    # 4. Delete session turns for project from SQLite
    try:
        db_path = Path(__file__).parents[4] / "data" / "sessions.db"
        if db_path.exists():
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute("DELETE FROM conversation_turns WHERE project_id = ?", (project,))
                conn.commit()
        results["session_turns"] = "cleared"
    except Exception:
        logger.exception("admin/clear: SQLite clear failed")
        results["session_turns"] = "error"

    return {"status": "reset_complete", "project": project, "results": results}


# ── Ingest — Jira ─────────────────────────────────────────────────────────────

@limiter.limit("5/minute")
@router.post("/admin/ingest/jira", response_model=JiraIngestResponse)
async def admin_ingest_jira(
    request: Request,
    body:    JiraIngestRequest,
    user: UserContext = Depends(get_admin_user),
):
    """
    Fetch recent Jira tickets and ingest them into Qdrant for RAG historical search.

    Fetches: open + recently resolved tickets, normalises to text, ingests as type='ticket'.
    Falls back to mock data when JIRA_TOKEN is a placeholder.
    """
    logger.info(
        "admin/ingest/jira: project='%s' max=%d user='%s'",
        body.project, body.max_tickets, user.name,
    )
    try:
        from backend.mcp.connectors.jira_connector import JiraConnector
        from backend.rag.pipeline import RAGPipeline

        connector = JiraConnector(name="jira", connector_config={})
        if not connector.is_available():
            raise HTTPException(
                status_code=400,
                detail="Jira credentials are not configured. Please set JIRA_TOKEN in .env to enable Jira ingestion."
            )

        start = time.monotonic()

        sprint_board    = await connector.get_sprint_board(body.project)
        blocked_tickets = await connector.get_blocked_tickets(body.project)
        all_tickets     = await connector.search_tickets("", body.project)

        seen: set[str] = set()
        tickets: list[dict] = []
        for t in [*all_tickets, *blocked_tickets]:
            if t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                tickets.append(t)

        tickets = tickets[: body.max_tickets]

        if not tickets:
            return JiraIngestResponse(
                chunks_ingested=0,
                tickets_fetched=0,
                duration_seconds=0.0,
                message=f"No tickets found for project '{body.project}'.",
            )

        pipeline = RAGPipeline(use_llm_context=False)
        source   = "jira_tickets"

        try:
            pipeline.vector_store.mark_stale(project=body.project, source=source)
        except Exception:
            pass

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
            count = pipeline._ingest_text(
                text=text,
                doc_title=f"{ticket.get('id', '')} — {ticket.get('title', '')[:80]}",
                doc_type="ticket",
                metadata={
                    "project":   body.project,
                    "source":    source,
                    "type":      "ticket",
                    "doc_title": f"{ticket.get('id', '')} — {ticket.get('title', '')[:80]}",
                    "url":       ticket.get("url", ""),
                },
            )
            total += count

        duration = time.monotonic() - start
        msg = (
            f"Ingested {total} chunks from {len(tickets)} Jira tickets "
            f"(project='{body.project}') in {duration:.1f}s."
        )
        logger.info("admin/ingest/jira: %s", msg)
        return JiraIngestResponse(
            chunks_ingested=total,
            tickets_fetched=len(tickets),
            duration_seconds=round(duration, 2),
            message=msg,
        )

    except Exception:
        logger.exception("admin/ingest/jira: failed")
        raise HTTPException(status_code=500, detail="Jira ingest failed. Check server logs.")


# ── Cross-Document Links ──────────────────────────────────────────────────────

@limiter.limit("2/minute")
@router.post("/admin/build-links", response_model=CrossDocLinksResponse)
async def admin_build_links(
    request:        Request,
    project:        str   = Query(default=settings.DEFAULT_PROJECT),
    min_similarity: float = Query(default=0.75, ge=0.0, le=1.0, description="Minimum cosine similarity to create a link"),
    user: UserContext = Depends(get_admin_user),
):
    """
    Build cross-document links between semantically related chunks across sources.
    Run once after all sources are ingested.
    """
    logger.info(
        "admin/build-links: project='%s' min_sim=%.2f user='%s'",
        project, min_similarity, user.name,
    )
    try:
        from backend.rag.pipeline import RAGPipeline

        pipeline = RAGPipeline(use_llm_context=False)
        start    = time.monotonic()
        linked   = pipeline.build_cross_document_links(project, min_similarity=min_similarity)
        duration = time.monotonic() - start

        msg = (
            f"Built cross-document links for {linked} chunks "
            f"(project='{project}', min_similarity={min_similarity}) in {duration:.1f}s."
        )
        logger.info("admin/build-links: %s", msg)
        return CrossDocLinksResponse(
            chunks_linked=linked,
            duration_seconds=round(duration, 2),
            message=msg,
        )
    except Exception:
        logger.exception("admin/build-links: failed")
        raise HTTPException(status_code=500, detail="Cross-document linking failed. Check server logs.")


# ── Test Slack ────────────────────────────────────────────────────────────────

@limiter.limit("5/minute")
@router.post("/admin/test-slack")
async def admin_test_slack(
    request: Request,
    user: UserContext = Depends(get_admin_user),
):
    """
    Send a test Slack notification immediately — don't wait for 5pm scheduler.
    Runs a full sprint risk scan and posts to the configured Slack channel.
    """
    logger.info("admin/test-slack: triggered by %s", user.name)
    try:
        from backend.core.scheduler import _run_risk_scan
        await _run_risk_scan()
        return {
            "status": "ok",
            "message": (
                "Risk scan completed. If SLACK_USE_MOCK=false and SLACK_BOT_TOKEN is set, "
                "a message was posted to your #engineering-manager channel. "
                "Check uvicorn logs for details."
            ),
        }
    except Exception:
        logger.exception("admin/test-slack: failed")
        raise HTTPException(
            status_code=500,
            detail="Test risk scan failed. Check server logs for details.",
        )
