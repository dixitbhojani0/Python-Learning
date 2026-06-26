"""
backend/api/routes/admin/models.py

Pydantic request and response models for the admin API.
Keeping models separate from endpoint logic makes them easy to import,
test, and reuse across the admin router.
"""
from pydantic import BaseModel

from backend.core.settings import settings


# ── Request Models ─────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    """Body for POST /admin/ingest."""
    project:   str  = settings.DEFAULT_PROJECT   # project tag applied to all ingested chunks
    use_llm:   bool = False          # True = call Groq for context prefixes (slower)
    directory: str  = ""             # optional override; default = all data/ dirs


class ConfluenceIngestRequest(BaseModel):
    """Body for POST /admin/ingest/confluence."""
    project:    str = settings.DEFAULT_PROJECT
    space_key:  str = settings.CONFLUENCE_SPACE_KEY


class JiraIngestRequest(BaseModel):
    """Body for POST /admin/ingest/jira."""
    project:     str = settings.DEFAULT_PROJECT
    max_tickets: int = 100


# ── Response Models ────────────────────────────────────────────────────────────

class IngestResponse(BaseModel):
    chunks_ingested:  int
    duration_seconds: float
    message:          str


class ConfluenceIngestResponse(BaseModel):
    chunks_ingested:  int
    pages_fetched:    int
    duration_seconds: float
    message:          str


class JiraIngestResponse(BaseModel):
    chunks_ingested:  int
    tickets_fetched:  int
    duration_seconds: float
    message:          str


class CrossDocLinksResponse(BaseModel):
    chunks_linked:    int
    duration_seconds: float
    message:          str


class StatsResponse(BaseModel):
    qdrant_chunks:   int
    qdrant_collection: str
    redis_keys:      int
    session_turns:   int
    semantic_facts:  int
    app_env:         str
    default_project: str
