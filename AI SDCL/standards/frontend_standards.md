# Frontend Standards — Chainlit Chat UI

Rules for the Chainlit frontend. One core rule covers everything: the frontend is a thin client. Nothing else.

---

## Core Rule — Zero Business Logic in Frontend

The Chainlit app (`frontend/app.py`) is a UI shell. It:
- Collects user input
- Sends HTTP requests to FastAPI
- Displays streamed responses
- Shows approve/reject buttons when HITL is triggered

It does NOT:
- Import from `backend/`
- Call LangGraph directly
- Talk to Qdrant or Redis
- Embed text or classify intent
- Contain prompt templates

```python
# CORRECT — frontend calls FastAPI
import httpx

async def call_backend(message: str, token: str, project: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/chat",
            json={"message": message, "project": project},
            headers={"x-token": token},
        )
    return response.json()["response"]

# WRONG — frontend imports backend module
from backend.orchestrator.graph import graph   # ← never
from backend.rag.retriever import HybridRetriever  # ← never
```

---

## Authentication in Frontend

The demo token is stored in Chainlit's session state. Never hardcode tokens in the frontend source.

```python
@cl.on_chat_start
async def on_start():
    # Ask user which role they want
    res = await cl.AskActionMessage(
        content="Select your role:",
        actions=[
            cl.Action(name="developer", label="Developer"),
            cl.Action(name="manager", label="Project Manager"),
            cl.Action(name="stakeholder", label="Stakeholder"),
        ]
    ).send()

    role = res.get("value")
    # Map role to demo token from env (loaded into Chainlit's process environment)
    import os
    token_map = {
        "developer":   os.environ["DEMO_TOKEN_DEVELOPER"],
        "manager":     os.environ["DEMO_TOKEN_MANAGER"],
        "stakeholder": os.environ["DEMO_TOKEN_STAKEHOLDER"],
    }
    cl.user_session.set("x_token", token_map[role])
    cl.user_session.set("role", role)
    cl.user_session.set("project", "antlog")
```

---

## Message Handler — One Function

```python
@cl.on_message
async def on_message(message: cl.Message):
    token = cl.user_session.get("x_token")
    project = cl.user_session.get("project")

    # Create a streaming message placeholder
    msg = cl.Message(content="")
    await msg.send()

    # Call FastAPI — get stream_id first, then stream tokens
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Submit message
        chat_resp = await client.post(
            "http://localhost:8000/api/chat",
            json={"message": message.content, "project": project},
            headers={"x-token": token},
        )
        data = chat_resp.json()

        # Check if HITL approval needed
        if data.get("hitl_pending"):
            await show_hitl_buttons(data["hitl_id"], data["hitl_proposal"])
            return

        # Stream tokens
        stream_id = data["stream_id"]
        async with client.stream("GET", f"http://localhost:8000/api/stream/{stream_id}",
                                 headers={"x-token": token}) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    token_text = line[6:]
                    await msg.stream_token(token_text)

    await msg.update()
```

---

## HITL Approve/Reject Buttons

When the backend signals HITL required, show Chainlit action buttons. Never implement approval logic in frontend — just call the API.

```python
async def show_hitl_buttons(hitl_id: str, proposal: dict):
    proposal_text = f"**Proposed action**: {proposal.get('description', 'See details')}"
    actions = [
        cl.Action(name="approve", label="✓ Approve", value=hitl_id),
        cl.Action(name="reject",  label="✗ Reject",  value=hitl_id),
    ]
    await cl.Message(content=proposal_text, actions=actions).send()

@cl.action_callback("approve")
async def on_approve(action: cl.Action):
    token = cl.user_session.get("x_token")
    async with httpx.AsyncClient() as client:
        await client.post(
            "http://localhost:8000/api/hitl/approve",
            json={"hitl_id": action.value},
            headers={"x-token": token},
        )
    await cl.Message(content="Action approved. Resuming...").send()

@cl.action_callback("reject")
async def on_reject(action: cl.Action):
    token = cl.user_session.get("x_token")
    async with httpx.AsyncClient() as client:
        await client.post(
            "http://localhost:8000/api/hitl/reject",
            json={"hitl_id": action.value},
            headers={"x-token": token},
        )
    await cl.Message(content="Action rejected.").send()
```

---

## Chainlit Config — frontend/.chainlit/config.toml

```toml
[project]
name = "AI SDLC Assistant"
enable_telemetry = false

[UI]
name = "SDLC Assistant"
description = "AI-powered engineering team assistant"
default_collapse_content = true
hide_cot = true    # hide chain-of-thought in UI

[features]
spontaneous_file_upload.enabled = false   # enable in Phase 11 for doc upload
```

---

## Error Display

Always show user-friendly errors. Never show raw exception text.

```python
try:
    response = await client.post(...)
    response.raise_for_status()
except httpx.HTTPStatusError as e:
    await cl.Message(content=f"Server error ({e.response.status_code}). Please try again.").send()
except httpx.RequestError:
    await cl.Message(content="Cannot reach the backend. Is the server running?").send()
```

---

## Running Chainlit

```bash
# From project root
chainlit run frontend/app.py --port 8080 --watch

# --watch enables auto-reload on file change (like uvicorn --reload)
```

Backend must be running first: `uvicorn backend.api.main:app --reload --port 8000`

---

## httpx Timeout

Always set a timeout. LLM calls can be slow (5-15 seconds for long responses).

```python
async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
    ...
```

- `connect=5.0`: fail fast if backend is not reachable
- `timeout=30.0`: allow up to 30 seconds for LLM responses
