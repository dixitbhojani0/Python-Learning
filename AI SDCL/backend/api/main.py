"""
backend/api/main.py

FastAPI application entry point.

This file does three things:
  1. Creates the FastAPI app instance
  2. Registers middleware (CORS)
  3. Registers all route files (routers)

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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config_loader import config
from backend.core.settings import settings

logger = logging.getLogger(__name__)


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


# ── CORS Middleware ────────────────────────────────────────────────────────────
#
# CORS = Cross-Origin Resource Sharing.
#
# By default, browsers block requests from one origin to another.
# Our Chainlit UI runs on localhost:8080 and calls FastAPI on localhost:8000.
# Different ports = different origins. Without this middleware, the browser
# would reject every request from Chainlit to FastAPI.
#
# CORSMiddleware adds HTTP headers to every response that tell the browser:
# "it's safe for localhost:8080 and localhost:3000 to call this server."

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",    # Chainlit default port
        "http://localhost:3000",    # React/Next.js (future frontend migration)
        "http://localhost:8501",    # Streamlit admin panel
    ],
    allow_credentials=True,         # allows cookies and auth headers
    allow_methods=["*"],            # GET, POST, PUT, DELETE, OPTIONS
    allow_headers=["*"],            # including our custom x-token header
)


# ── Health endpoint ────────────────────────────────────────────────────────────
#
# GET /health — no auth required.
# Used by Docker healthchecks, monitoring tools, and team members to
# verify the server is up before making real requests.
#
# Deliberately simple: if this endpoint returns 200, the server process is alive
# and Python imports worked. It's not a deep health check (doesn't ping Qdrant
# or Redis — those checks come in Phase 9 when we add the memory layer).

@app.get("/health")
async def health():
    """Server liveness check — returns 200 if the app is running."""
    return {
        "status":  "ok",
        "app":     "AI SDLC Assistant",
        "env":     settings.APP_ENV,
        "project": settings.DEFAULT_PROJECT,
    }


# ── Routers — added incrementally per phase ────────────────────────────────────
#
# Each route file creates its own APIRouter and we include it here with a prefix.
# prefix="/api" means @router.post("/chat") becomes POST /api/chat automatically.
#
# Phase 7a: hitl router    (POST /api/hitl/approve|reject)
# Phase 11: admin router   (GET/POST /admin/*)

from backend.api.routes import chat        # Phase 3b
from backend.api.routes import hitl        # Phase 7a
app.include_router(chat.router, prefix="/api")
app.include_router(hitl.router, prefix="/api")
