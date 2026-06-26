"""
backend/api/main.py

FastAPI application entry point.

This file does five things:
  1. Creates the FastAPI app instance with lifespan startup/shutdown
  2. Registers security middleware (headers, request size, rate limiting, CORS)
  3. Wires the slowapi rate limiter and exception handler
  4. Registers all route files (routers)
  5. Exposes the /health liveness endpoint

It does NOT contain any business logic — no LLM calls, no RAG, no agents.
Those live in routes/ and are imported here.

Run the server:
    uvicorn backend.api.main:app --reload --port 8000

Why uvicorn?
  FastAPI is an ASGI application — it defines how to handle requests.
  Uvicorn is the ASGI server — it actually listens on a port and passes
  incoming HTTP requests to FastAPI. You need both.

Why --reload?
  Watches for Python file changes and restarts the server automatically.
  Only use in development. In production: remove --reload, add --workers 2.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from backend.api.limiter import limiter
from backend.core.config_loader import config
from backend.core.settings import settings

logger = logging.getLogger(__name__)


# ── Security Headers Middleware ────────────────────────────────────────────────
#
# Adds security headers to EVERY HTTP response at zero performance cost.
#
# X-Content-Type-Options: nosniff
#   Prevents browsers from MIME-sniffing — guessing the content type.
#   Without this, a file that claims to be PDF but contains JS could be executed.
#
# X-Frame-Options: DENY
#   Prevents embedding this API in an <iframe> (clickjacking defense).
#   This is an API, not a UI — it should never be embedded.
#
# X-XSS-Protection: 0
#   Disables the browser's old built-in XSS filter. Modern OWASP guidance
#   recommends disabling it — the old filter had exploitable side-channels.
#   CSP (Content-Security-Policy) replaces it.
#
# Content-Security-Policy: default-src 'self'
#   Only load resources from the same origin. Belt-and-suspenders for an API
#   that returns JSON (the Chainlit UI enforces its own CSP separately).
#
# Strict-Transport-Security: max-age=31536000
#   Tells browsers to use HTTPS for this domain for the next year.
#   Only meaningful in production with a real TLS cert — harmless in dev.
#
# Referrer-Policy: strict-origin-when-cross-origin
#   Prevents leaking query strings or session IDs to external services
#   via the Referer header.

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ── Request Body Size Limit Middleware ─────────────────────────────────────────
#
# Rejects requests with bodies larger than MAX_BYTES BEFORE reading the full body.
#
# WHY: The Pydantic max_length=2000 validator runs AFTER the entire request body
# is loaded into RAM. A malicious client can send a 50MB fake JSON payload that
# consumes 50MB of server RAM per worker before Pydantic even looks at it.
# This middleware cuts off oversized payloads at the HTTP layer.
#
# 10KB is intentionally generous: a 2000-char message at ~4 bytes/char = 8KB
# plus JSON overhead fits comfortably. Legitimate traffic is always under 10KB.
#
# We check Content-Length header, not the actual body — reading the body to
# check its size would defeat the purpose. A client that lies about Content-Length
# will still be caught by Pydantic's max_length validator downstream.

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    MAX_BYTES = 10 * 1024  # 10 KB

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self.MAX_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "payload_too_large",
                        "detail": (
                            f"Request body exceeds {self.MAX_BYTES} bytes. "
                            "Maximum message length is 2000 characters."
                        ),
                    },
                )
        return await call_next(request)


def _configure_langsmith() -> None:
    """
    Export LangSmith env vars from Pydantic Settings into os.environ.

    LangChain's tracing middleware reads os.environ directly — it doesn't
    know about our Settings object. Pydantic Settings reads .env into a
    Python dataclass but does NOT write to os.environ. We bridge the gap here,
    once at startup, before any LangChain/LangGraph code is imported.
    """
    os.environ["LANGCHAIN_TRACING_V2"] = settings.LANGCHAIN_TRACING_V2
    os.environ["LANGCHAIN_API_KEY"]    = settings.LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_PROJECT"]    = settings.LANGCHAIN_PROJECT
    os.environ["LANGCHAIN_ENDPOINT"]   = settings.LANGCHAIN_ENDPOINT

    if settings.LANGCHAIN_TRACING_V2.lower() == "true":
        logger.info(
            "LangSmith tracing ENABLED — project='%s' endpoint='%s'",
            settings.LANGCHAIN_PROJECT,
            settings.LANGCHAIN_ENDPOINT,
        )
    else:
        logger.info("LangSmith tracing DISABLED (set LANGCHAIN_TRACING_V2=true in .env to enable)")


def _validate_model_config() -> None:
    """
    Warn at startup if GROQ_MODEL is not in the known-good list from llm.yaml.

    WHY WARNING not crash: Groq adds new models frequently. An unknown model
    may still be valid — we just don't have it in our list yet. Crashing would
    break new deployments on new model names. WARNING gives visibility without
    being brittle. Check https://console.groq.com/docs/models for the current list.
    """
    model = settings.GROQ_MODEL
    known_models: list[str] = config.get_llm_config().get("primary", {}).get("known_models", [])
    if known_models and model not in known_models:
        logger.warning(
            "Startup: GROQ_MODEL='%s' is not in the known-good list. "
            "Verify the model name at https://console.groq.com/docs/models. "
            "Known models: %s",
            model,
            sorted(known_models),
        )
    else:
        logger.info("Startup: GROQ_MODEL='%s' validated", model)


# ── Lifespan — runs startup/shutdown code around the server ───────────────────
#
# @asynccontextmanager turns this into a context manager:
#   - everything BEFORE yield runs at startup (before any request is handled)
#   - everything AFTER yield runs at shutdown (when you Ctrl+C the server)
#
# Why pre-warm here?
#   The sentence-transformers embedding model takes ~3 seconds to load on first use.
#   If we let it load lazily (on the first request), the first user gets a slow response.
#   Loading it in lifespan means it's cached in memory before anyone connects.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ───────────────────────────────────────────────────────────────
    logger.info("AI SDLC Assistant starting up...")
    logger.info("Environment : %s", settings.APP_ENV)
    logger.info("Default project: %s", settings.DEFAULT_PROJECT)

    # Configure LangSmith FIRST — before importing LangChain/LangGraph modules
    _configure_langsmith()

    # Validate Groq model name — fail-fast warning if misconfigured
    _validate_model_config()

    # Pre-warm the embedding model — loads ~90MB model into RAM now
    # so the first /api/chat request isn't slow
    try:
        from backend.rag.retriever import _get_embed_model
        _get_embed_model()
        logger.info("Startup: embedding model loaded and ready")
    except Exception:
        logger.exception("Startup: failed to pre-load embedding model")

    # Pre-warm the reranker model — loads cross-encoder into RAM
    try:
        from backend.rag.retriever import _get_rerank_model
        _get_rerank_model()
        logger.info("Startup: reranker model loaded and ready")
    except Exception:
        logger.exception("Startup: failed to pre-load reranker model")

    # Start APScheduler — proactive sprint risk scan at 5pm weekdays
    try:
        from backend.core.scheduler import start_scheduler
        start_scheduler()
    except Exception:
        logger.exception("Startup: failed to start scheduler — continuing without it")

    logger.info("AI SDLC Assistant ready — listening on port 8000")

    yield   # ← server is running here, handling requests

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    try:
        from backend.core.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    logger.info("AI SDLC Assistant shutting down")


# ── FastAPI app instance ───────────────────────────────────────────────────────
#
# title and description appear in the auto-generated docs at /docs
# lifespan= wires in our startup/shutdown logic above

app = FastAPI(
    title="AI SDLC Assistant",
    description="RAG-powered multi-agent assistant for software delivery teams",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Rate limiter wiring ────────────────────────────────────────────────────────
#
# app.state.limiter: slowapi reads the limiter from here when processing
#   @limiter.limit() decorators on route functions.
#
# _rate_limit_exceeded_handler: when a limit is exceeded, slowapi raises
#   RateLimitExceeded. Without the handler FastAPI returns 500. With it,
#   the client gets a clean 429 with a Retry-After header.

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Middleware stack ───────────────────────────────────────────────────────────
#
# Starlette executes middleware in REVERSE registration order (last registered = first executed).
# Registration order below → execution order on each request:
#   1. CORSMiddleware              — handle cross-origin preflight FIRST (outermost)
#   2. RequestSizeLimitMiddleware  — reject oversized bodies
#   3. SecurityHeadersMiddleware   — add security headers to every response
#
# WHY CORSMiddleware must be outermost (added last):
#   Our SecurityHeadersMiddleware and RequestSizeLimitMiddleware both extend
#   BaseHTTPMiddleware. There is a known Starlette issue where BaseHTTPMiddleware
#   wrapping CORSMiddleware breaks OPTIONS preflight — the BaseHTTPMiddleware
#   intercepts the streaming response from CORSMiddleware before it reaches the
#   browser, causing 400 Bad Request on every OPTIONS request.
#   Making CORS outermost means OPTIONS preflights are handled entirely by
#   CORSMiddleware before BaseHTTPMiddleware ever touches the request.

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    # allow_origin_regex covers all localhost ports (4200, 4201, etc.) so the
    # Angular dev server never needs a manual entry when ng picks a different port.
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "x-token"],
)


# ── Health endpoint ────────────────────────────────────────────────────────────
#
# GET /health — no auth required.
# Used by Docker healthchecks, monitoring tools, and team members to
# verify the server is up before making real requests.

@limiter.limit("30/minute")
@app.get("/health")
async def health(request: Request):
    """Server liveness check — returns 200 if the app is running."""
    return {
        "status":  "ok",
        "app":     "AI SDLC Assistant",
        "env":     settings.APP_ENV,
        "project": settings.DEFAULT_PROJECT,
    }


# ── Routers ───────────────────────────────────────────────────────────────────
#
# Each route file creates its own APIRouter and we include it here with a prefix.
# prefix="/api" means @router.post("/chat") becomes POST /api/chat automatically.
#
# chat   — POST /api/chat                        (main conversation endpoint)
# stream — GET  /api/stream/{stream_id}          (SSE token streaming)
# hitl   — POST /api/hitl/approve|reject         (Human-in-the-Loop approval)
# admin  — GET|POST /admin/*                     (admin operations, manager-only)

from backend.api.routes import chat        # POST /api/chat
from backend.api.routes import stream      # GET  /api/stream/{id}
from backend.api.routes import hitl        # POST /api/hitl/approve|reject
from backend.api.routes import admin       # GET|POST /admin/*
from backend.api.routes import webhooks    # POST /webhooks/github

app.include_router(chat.router,   prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(hitl.router,   prefix="/api")
app.include_router(admin.router)
app.include_router(webhooks.router)

# ── Static images ───────────────────────────────────────────────────────────
# Document images extracted at ingestion (data/images/) are served read-only at
# GET /images/{file}. ChatResponse.images[].url points here so the UI can render
# them. Created if missing so the mount never fails on a fresh checkout.
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_IMAGES_DIR = Path(__file__).parents[2] / "data" / "images"
_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(_IMAGES_DIR)), name="images")
