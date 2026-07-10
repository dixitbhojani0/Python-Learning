"""FinAdvisor AI — Advisor Copilot backend.

Run (from backend/):  uvicorn app.main:app --port 8123
Serves the built frontend (frontend/dist) at http://localhost:8123 when present.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routers import knowledge, logs, script, session

app = FastAPI(title="FinAdvisor AI — Advisor Copilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(session.router, prefix="/api/v1")
app.include_router(script.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(logs.router, prefix="/api/v1")

DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
