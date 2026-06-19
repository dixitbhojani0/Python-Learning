# Backend Standards — FastAPI API Layer

Rules for all FastAPI routes, schemas, authentication, and streaming.

---

## Core Principle — FastAPI is the Permanent Contract

The API endpoints defined here will never change, even when the frontend is migrated from Chainlit to Next.js. Backend has zero knowledge of which frontend consumes it.

```
POST /api/chat         → Submit a user message
GET  /api/stream/{id}  → Stream the response (SSE)
POST /api/hitl/approve → Approve a pending HITL action
POST /api/hitl/reject  → Reject a pending HITL action
GET  /api/sessions     → Fetch session history

POST /admin/ingest     → Trigger RAG ingestion
GET  /admin/chunks     → Browse Qdrant chunks
PUT  /admin/config/llm → Update LLM config live
GET  /admin/sessions   → All user sessions (admin)
GET  /admin/metrics    → Token usage, latency, cache stats
```

---

## FastAPI App Setup — backend/api/main.py

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic. Runs once."""
    # Startup: initialize DB, load models, warm caches
    await init_db()
    _ = get_retriever()   # triggers model download if needed
    yield
    # Shutdown: cleanup (optional for demo)

app = FastAPI(title="AI SDLC Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
from backend.api.routes import chat, stream, hitl, admin
app.include_router(chat.router,   prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(hitl.router,   prefix="/api")
app.include_router(admin.router,  prefix="/admin")
```

---

## Route File Organization

One file per resource group. Never put unrelated routes in the same file.

```
backend/api/routes/
  chat.py      ← POST /api/chat
  stream.py    ← GET /api/stream/{id}
  hitl.py      ← POST /api/hitl/approve|reject
  sessions.py  ← GET /api/sessions
  admin.py     ← All /admin/* routes
  webhooks.py  ← POST /webhooks/github (future)
```

Each file creates its own `router`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.post("/chat")
async def chat(request: ChatRequest, user: UserContext = Depends(get_current_user)):
    ...
```

---

## Pydantic Schemas — All in backend/api/models/schemas.py

Every request body and response body must be a Pydantic model. Never use raw dicts in route signatures.

```python
# backend/api/models/schemas.py
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    project: str = Field(default="antlog")
    session_id: str | None = Field(default=None)   # None = new session

class ChatResponse(BaseModel):
    response: str
    confidence: float
    sources: list[str]
    session_id: str
    strategy: str       # "first_pass" | "corrective" | "degraded"

class HITLRequest(BaseModel):
    hitl_id: str

class ErrorResponse(BaseModel):
    error: str
    detail: str
```

---

## Authentication — Always Use Depends()

Never check tokens inline in route handlers. Always use the dependency.

```python
# CORRECT
@router.post("/chat")
async def chat(
    request: ChatRequest,
    user: UserContext = Depends(get_current_user),   # auth injected
):
    # user.role, user.project are available
    ...

# WRONG — inline auth check
@router.post("/chat")
async def chat(request: ChatRequest, x_token: str = Header(...)):
    if x_token not in DEMO_USERS:    # ← put this in middleware, not here
        raise HTTPException(401)
```

Admin routes use `get_admin_user`:
```python
@router.post("/admin/ingest")
async def trigger_ingest(user: UserContext = Depends(get_admin_user)):
    ...
```

---

## Error Responses — Always Structured

Never let raw exceptions surface to the client. Use `HTTPException` with structured detail.

```python
# CORRECT
raise HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail={"error": "invalid_project", "detail": f"Project '{project}' not found in registry"}
)

# WRONG — raw exception message leaks internals
raise Exception("KeyError: 'antlog' not in mcp_registry")
```

Standard error response format (always `error` + `detail`):
```json
{"error": "invalid_project", "detail": "Project 'xyz' not found in the MCP registry"}
```

---

## SSE Streaming — GET /api/stream/{id}

```python
from fastapi.responses import StreamingResponse
import asyncio

@router.get("/stream/{stream_id}")
async def stream_response(stream_id: str, user: UserContext = Depends(get_current_user)):
    async def token_generator():
        # Read tokens from Redis stream buffer
        while True:
            token = await redis.lpop(f"stream:response:{stream_id}")
            if token == "[DONE]":
                break
            if token:
                yield f"data: {token}\n\n"   # SSE format
            else:
                await asyncio.sleep(0.01)

    return StreamingResponse(
        token_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

---

## All Route Handlers Must Be Async

```python
# CORRECT
@router.post("/chat")
async def chat(request: ChatRequest, user: UserContext = Depends(get_current_user)):
    result = await graph.ainvoke(initial_state)
    return ChatResponse(...)

# WRONG — synchronous handler blocks the event loop
@router.post("/chat")
def chat(request: ChatRequest):
    result = graph.invoke(initial_state)   # blocks all other requests
    ...
```

---

## Routes Must NOT Import Agents or RAG Directly

Routes talk to the orchestrator only. The orchestrator talks to agents. Agents talk to MCP and RAG.

```python
# CORRECT — route calls orchestrator
from backend.orchestrator.graph import graph

@router.post("/chat")
async def chat(request: ChatRequest, user: UserContext = Depends(get_current_user)):
    result = await graph.ainvoke(initial_state)
    ...

# WRONG — route imports agent directly (breaks layering)
from backend.agents.cross_source_agent import CrossSourceAgent
@router.post("/chat")
async def chat(...):
    agent = CrossSourceAgent(...)
    payload = await agent.run(state)
```

---

## FastAPI Startup — Run with uvicorn

Development:
```bash
uvicorn backend.api.main:app --reload --port 8000 --host 0.0.0.0
```

`--reload` watches for file changes and restarts. Only use in development.

Production (future):
```bash
uvicorn backend.api.main:app --workers 2 --port 8000 --host 0.0.0.0
```
