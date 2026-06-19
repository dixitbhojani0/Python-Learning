# Python Coding Standards — AI SDLC Assistant

Apply these rules to every `.py` file in this project.

---

## Python Version

- **Python 3.11+** required.
- Use `match` statements instead of long `if/elif` chains where it improves clarity.
- Use `tomllib` for TOML if needed (built-in from 3.11).

---

## Type Hints

Always annotate function signatures. No exceptions.

```python
# CORRECT
def retrieve(query: str, project: str, top_k: int = 7) -> list[RetrievedChunk]:
    ...

async def complete(prompt: str) -> str:
    ...

# WRONG — missing annotations
def retrieve(query, project):
    ...
```

- Use `list[T]`, `dict[K, V]`, `tuple[A, B]` (lowercase, Python 3.9+ style).
- Use `T | None` instead of `Optional[T]`.
- Use `Any` only when the type is genuinely unknown at design time (e.g. LangGraph state dict values). Add a comment explaining why.
- Return types are mandatory on all public methods.

---

## Naming Conventions

| Thing | Convention | Example |
|-------|------------|---------|
| Module/file | `snake_case` | `cross_source_agent.py` |
| Class | `PascalCase` | `HybridRetriever` |
| Function / method | `snake_case` | `retrieve_with_corrective_rag` |
| Variable | `snake_case` | `query_embedding` |
| Constant (module-level) | `UPPER_SNAKE` | `TOTAL_INPUT_BUDGET = 8000` |
| Private method | `_snake_case` | `_generate_context_prefix` |
| LangGraph node function | `snake_case` | `classify_intent` |

---

## Imports

Order: stdlib → third-party → internal. One blank line between each group.

```python
# stdlib
import logging
import uuid
from pathlib import Path
from typing import Any

# third-party
from langchain_groq import ChatGroq
from pydantic import BaseModel

# internal
from backend.core.config_loader import config
from backend.core.settings import settings
from backend.rag.retriever import HybridRetriever
```

- Never use `from module import *`.
- Never use circular imports. If you find one, restructure.
- Group internal imports by layer (core → rag → agents → api).

---

## Async Rules

- All I/O-bound operations (Qdrant, Redis, HTTP calls, file reads in routes) must be `async`.
- All FastAPI route handlers must be `async def`.
- All agent `run()` methods must be `async def`.
- CPU-bound operations (embedding computation, BM25 scoring, reranking) can be synchronous — they run in the calling thread without blocking the event loop significantly for our scale.
- Use `asyncio.to_thread()` only if a sync call genuinely blocks for > 100ms inside an async context.

```python
# CORRECT — async for IO
async def save_session(session_id: str, data: dict) -> None:
    async with AsyncSession(engine) as session:
        ...

# CORRECT — sync is fine for CPU-local operations
def embed_text(text: str) -> list[float]:
    return model.encode(text).tolist()
```

---

## Error Handling

- Log the error at the point of failure. Re-raise or return a safe fallback — never swallow silently.
- Use `logger.exception()` (not `logger.error()`) when catching unexpected exceptions — it includes the traceback.
- Use specific exception types. Catch `Exception` only as a last resort.

```python
# CORRECT
try:
    response = llm.invoke(prompt)
    return response.content.strip()
except Exception as e:
    logger.exception("LLM call failed for prompt key '%s'", prompt_key)
    return ""   # safe fallback

# WRONG — silent swallow
try:
    response = llm.invoke(prompt)
except:
    pass
```

---

## Logging

- Every module must have: `logger = logging.getLogger(__name__)` at the top.
- No `print()` statements in any file under `backend/`, `admin/`, or `scripts/`.
- Log levels:
  - `DEBUG` — per-chunk or per-request detail (only visible in debug mode)
  - `INFO` — meaningful state changes (file ingested, agent selected, cache hit)
  - `WARNING` — non-critical but unexpected (config key missing, fallback used)
  - `ERROR` / `EXCEPTION` — failures that affect functionality

```python
logger.info("HybridRetriever: returning %d chunks, top score=%.3f", len(chunks), top_score)
logger.warning("ConfigLoader: prompt key '%s' not found — returning empty", key)
logger.exception("VectorStore: upsert failed for chunk %s", chunk_id)
```

---

## Comments

- Write comments only for **WHY**, not WHAT.
- One line max. No multi-paragraph comment blocks.
- No TODO comments in committed code — use GitHub issues for that.

```python
# CORRECT — explains a non-obvious constraint
k = 60  # standard RRF paper value (Cormack et al., 2009) — do not change without benchmarking

# WRONG — narrates what the code already says
# Loop through chunks and add to the list
for chunk in chunks:
    result.append(chunk)
```

---

## Classes

- Use `@dataclass` for simple data containers without behaviour.
- Use `BaseModel` (Pydantic) for API request/response models and validated data.
- Use regular classes for services (retriever, pipeline, registry).
- Abstract base classes use `ABC` + `@abstractmethod`.

```python
# Data container → dataclass
@dataclass
class RetrievedChunk:
    text: str
    parent_text: str
    score: float

# API model → Pydantic BaseModel
class ChatRequest(BaseModel):
    message: str
    project: str = "antlog"

# Service → regular class
class HybridRetriever:
    def __init__(self): ...
    def retrieve(self, query: str, project: str) -> list[RetrievedChunk]: ...
```

---

## Module-Level Singletons

Expensive objects (embedding models, reranker models, LLM clients, DB engines) are loaded **once** at module import time or at startup, not per-request.

```python
# CORRECT — load once
_EMBED_MODEL: SentenceTransformer | None = None

def _get_embed_model() -> SentenceTransformer:
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBED_MODEL

# WRONG — loads on every request
def embed_text(text: str) -> list[float]:
    model = SentenceTransformer("all-MiniLM-L6-v2")  # ← expensive, never do this per-call
    return model.encode(text).tolist()
```

---

## File Size Guideline

- Keep files under ~250 lines. If a file grows beyond that, consider splitting by responsibility.
- One class or one logical group of functions per file.
- Never put unrelated things in the same file because it's "convenient".
