"""
backend/api/models/schemas.py

Pydantic models for all API request bodies and responses.

Why Pydantic models (not plain dicts)?
  1. Automatic validation — FastAPI rejects wrong types before your code runs
  2. Self-documenting — /docs shows exact field types and constraints
  3. Type safety — IDE autocomplete works on request.message, not request["message"]
  4. Response filtering — ChatResponse ensures we never leak internal fields to clients
"""
from pydantic import BaseModel, Field

from backend.core.settings import settings


class ChatRequest(BaseModel):
    """
    Body of POST /api/chat.

    session_id: if None, a new session is created and returned in the response.
                On follow-up messages, pass the session_id from the previous response
                to maintain conversation context (used in Phase 9 — memory layer).
    """
    message:    str       = Field(...,          min_length=1, max_length=2000)
    project:    str       = Field(default=settings.DEFAULT_PROJECT)
    session_id: str | None = Field(default=None)


class ImageRef(BaseModel):
    """A document image relevant to the answer — served from /images/{file}."""
    url:       str            # e.g. "/images/690ea3b8.png" — GET-able from the API
    doc_title: str = ""       # which document the image came from
    page:      int | None = None
    caption:   str = ""       # OCR excerpt / placeholder, for alt text + display


class ChatResponse(BaseModel):
    """
    Response from POST /api/chat.

    confidence: reranker score of the top retrieved chunk.
                Phase 3b: raw CrossEncoder logit (e.g. 6.752).
                Phase 5+: will be normalized to 0–1 and used for confidence tiers.

    sources:    unique data sources that contributed to the answer
                e.g. ["local", "slack"] — from the retrieved chunks' source field.

    strategy:   how the answer was produced:
                "first_pass" — normal retrieval succeeded
                "corrective" — first retrieval was low confidence, reformulated and retried
                "degraded"   — no confident evidence found, answer is a fallback
    """
    response:        str
    confidence:      float
    sources:         list[str]
    session_id:      str
    stream_id:       str        = ""   # open GET /api/stream/{stream_id} for SSE token feed
    strategy:        str
    agent:           str        = ""   # which agent handled the query (intent) — shown as a UI chip
    relevancy:       float      = 0.0  # answer↔query cosine — answer-quality signal
    faithfulness:    float      = 0.0  # claims grounded in retrieved evidence (LLM judge) — hallucination signal
    hitl_required:   bool       = False
    hitl_action_id:  str | None = None
    response_cached: bool       = False
    images:          list[ImageRef] = []   # document images relevant to the answer


class HITLRequest(BaseModel):
    """Body for POST /api/hitl/approve and /api/hitl/reject (Phase 7a)."""
    hitl_id: str


class ErrorResponse(BaseModel):
    """Standard error shape. Always two fields: error (machine code) + detail (human message)."""
    error:  str
    detail: str
