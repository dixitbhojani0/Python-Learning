"""
frontend/app.py

Chainlit chat UI — thin client that talks to FastAPI only.

Architecture rule: this file contains ZERO business logic.
  - No imports from backend/
  - No direct RAG, LLM, or agent calls
  - Only sends HTTP requests to FastAPI and displays the responses

Two event handlers:
  @cl.on_chat_start  — runs once when the user opens the browser tab
                        shows role selection buttons
  @cl.on_message     — runs every time the user sends a message
                        calls POST /api/chat, displays the response

Run:
    cd frontend
    chainlit run app.py --port 8080

FastAPI must be running on port 8000 before starting Chainlit.
"""
import httpx
import chainlit as cl

# ── FastAPI base URL — must match uvicorn port
FASTAPI_URL = "http://localhost:8000"

# ── Role → demo token mapping (matches .env DEMO_TOKEN_* values)
# These are the only tokens FastAPI's auth middleware accepts.
ROLE_TOKENS = {
    "Developer":    "dev_token_alice",
    "Manager":      "manager_token_bob",
    "Stakeholder":  "stakeholder_token_client",
}

# ── Default project (matches DEFAULT_PROJECT in settings)
DEFAULT_PROJECT = "antlog"


# ─────────────────────────────────────────────────────────────────────────────
#  STARTUP — runs once per browser session when the tab is opened
# ─────────────────────────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start():
    """
    Greet the user and ask them to pick a role.

    Why role selection first?
      Different roles get different response tones (set by persona prompts in prompts.yaml).
      Developer → technical precision, Manager → delivery language, Stakeholder → plain English.
      The selected role determines which demo token we send as the x-token header.

    cl.Action: renders a clickable button in the chat.
      name    — identifier, matched by @cl.action_callback("set_role")
      payload — dict of data attached to this button (Chainlit 2.5.5+ requires dict, not string)
      label   — text shown on the button in the UI
    """
    await cl.Message(
        content=(
            "👋 **Welcome to the AI SDLC Assistant**\n\n"
            "I can help you understand sprint status, identify blockers, "
            "track delivery risks, and answer questions about your project.\n\n"
            "**Please select your role to get started:**"
        ),
        actions=[
            cl.Action(name="set_role", payload={"role": "Developer"},   label="👨‍💻 Developer"),
            cl.Action(name="set_role", payload={"role": "Manager"},     label="📋 Manager"),
            cl.Action(name="set_role", payload={"role": "Stakeholder"}, label="📊 Stakeholder"),
        ],
    ).send()


@cl.action_callback("set_role")
async def on_role_selected(action: cl.Action):
    """
    Fires when the user clicks one of the role buttons.

    cl.user_session: Chainlit's per-tab key-value store.
      Lives for the lifetime of the browser tab.
      Safe to store role and token here — completely separate per user.

    action.payload: the dict passed in payload= when creating the cl.Action.
    """
    role  = action.payload["role"]
    token = ROLE_TOKENS[role]

    # Store role and token in this session — used on every subsequent message
    cl.user_session.set("role",     role)
    cl.user_session.set("token",    token)
    cl.user_session.set("session_id", None)   # set on first message, reused after

    await cl.Message(
        content=(
            f"✅ Signed in as **{role}**\n\n"
            f"You can now ask questions about the **{DEFAULT_PROJECT}** project.\n\n"
            "**Example questions:**\n"
            "- What is blocking the dashboard feature?\n"
            "- What is the sprint 12 risk level?\n"
            "- Was there a CORS error after the nginx change?\n"
            "- Are we on track for the sprint goal?"
        )
    ).send()

    # Remove the action buttons (they've served their purpose)
    await action.remove()


# ─────────────────────────────────────────────────────────────────────────────
#  HITL CALLBACKS — fire when user clicks Approve or Reject on a proposal
# ─────────────────────────────────────────────────────────────────────────────

async def _call_hitl_endpoint(endpoint: str, hitl_id: str, token: str) -> str:
    """POST to /api/hitl/approve or /api/hitl/reject and return the response text."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{FASTAPI_URL}/api/{endpoint}",
            headers={"x-token": token, "Content-Type": "application/json"},
            json={"hitl_id": hitl_id},
        )
        resp.raise_for_status()
    return resp.json().get("response", "Done.")


@cl.action_callback("hitl_approve")
async def on_hitl_approve(action: cl.Action):
    """User clicked Approve — execute the proposal."""
    hitl_id = action.payload["hitl_id"]
    token   = action.payload["token"]
    try:
        result_text = await _call_hitl_endpoint("hitl/approve", hitl_id, token)
        await cl.Message(content=result_text).send()
    except Exception as exc:
        await cl.Message(content=f"❌ Failed to approve: {exc}").send()
    await action.remove()


@cl.action_callback("hitl_reject")
async def on_hitl_reject(action: cl.Action):
    """User clicked Reject — discard the proposal."""
    hitl_id = action.payload["hitl_id"]
    token   = action.payload["token"]
    try:
        result_text = await _call_hitl_endpoint("hitl/reject", hitl_id, token)
        await cl.Message(content=result_text).send()
    except Exception as exc:
        await cl.Message(content=f"❌ Failed to reject: {exc}").send()
    await action.remove()


# ─────────────────────────────────────────────────────────────────────────────
#  MESSAGE HANDLER — runs every time the user sends a chat message
# ─────────────────────────────────────────────────────────────────────────────

@cl.on_message
async def on_message(message: cl.Message):
    """
    Receives the user's question, calls FastAPI, displays the answer.

    Flow:
      1. Check role is set — if not, remind user to pick a role
      2. Show a "thinking" indicator while waiting for FastAPI
      3. POST /api/chat with the user's message and their token
      4. Display the response with source attribution
      5. Handle errors gracefully

    httpx.AsyncClient:
      An async HTTP client (like requests, but async).
      We use it to call FastAPI from within Chainlit's async event handler.
      timeout=60 — Groq can take up to 30s; we allow 60s total.
    """
    # ── Guard: role must be selected before asking questions
    token = cl.user_session.get("token")
    role  = cl.user_session.get("role")

    if not token:
        await cl.Message(
            content="⚠️ Please select your role first by clicking one of the buttons above."
        ).send()
        return

    # ── Show thinking indicator while we wait for the response
    # cl.Message with an empty content shows a loading state in the UI
    thinking_msg = cl.Message(content="")
    await thinking_msg.send()

    try:
        # ── Call FastAPI POST /api/chat
        async with httpx.AsyncClient(timeout=60.0) as client:
            api_response = await client.post(
                f"{FASTAPI_URL}/api/chat",
                headers={
                    "x-token":      token,
                    "Content-Type": "application/json",
                },
                json={
                    "message":    message.content,
                    "project":    DEFAULT_PROJECT,
                    "session_id": cl.user_session.get("session_id"),
                    # None on first message → FastAPI generates a UUID and returns it
                    # Stored below and sent on every subsequent message for continuity
                },
            )
            api_response.raise_for_status()   # raises on 4xx/5xx

        data = api_response.json()

        # ── Store session_id so subsequent messages continue the same session
        # First message: FastAPI generates a UUID and returns it here.
        # Subsequent messages: we already have it, so only store on first response.
        returned_session_id = data.get("session_id")
        if returned_session_id and not cl.user_session.get("session_id"):
            cl.user_session.set("session_id", returned_session_id)

        # ── Format the response with source attribution
        response_text    = data.get("response", "No response received.")
        sources          = data.get("sources", [])
        confidence       = data.get("confidence", 0.0)
        hitl_required    = data.get("hitl_required", False)
        hitl_action_id   = data.get("hitl_action_id")
        response_cached  = data.get("response_cached", False)

        # Append source footer — three cases:
        #   1. Cache hit  → ⚡ Cached (agents didn't run, sources are always empty)
        #   2. Fresh + sources  → 📚 Sources list + confidence
        #   3. Fresh + no sources → ⚠️ warning (low RAG confidence or empty Qdrant)
        if response_cached:
            footer = "\n\n---\n⚡ *Cached response — retrieved from semantic cache*"
        elif sources:
            source_list = ", ".join(f"`{s}`" for s in sources)
            footer = f"\n\n---\n📚 *Sources: {source_list} | Confidence: {confidence:.2f}*"
        else:
            footer = "\n\n---\n⚠️ *No sources found — answer may be incomplete.*"

        if hitl_required and hitl_action_id:
            # ── HITL path: replace thinking msg with proposal + action buttons
            # We send a NEW message (not update thinking_msg) so the buttons
            # stay attached to the proposal message even after the user decides.
            await thinking_msg.remove()
            await cl.Message(
                content=response_text + footer,
                actions=[
                    cl.Action(
                        name="hitl_approve",
                        payload={"hitl_id": hitl_action_id, "token": token},
                        label="✅ Approve",
                    ),
                    cl.Action(
                        name="hitl_reject",
                        payload={"hitl_id": hitl_action_id, "token": token},
                        label="❌ Reject",
                    ),
                ],
            ).send()
        else:
            # ── Normal path: update the thinking placeholder with the answer
            thinking_msg.content = response_text + footer
            await thinking_msg.update()

    except httpx.TimeoutException:
        thinking_msg.content = (
            "⏱️ The request timed out. "
            "The LLM may be under load — please try again."
        )
        await thinking_msg.update()

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            thinking_msg.content = "🔒 Authentication failed. Please refresh and select your role."
        elif exc.response.status_code == 422:
            thinking_msg.content = "❌ Invalid request format. Please try rephrasing your question."
        else:
            thinking_msg.content = f"❌ Server error ({exc.response.status_code}). Please try again."
        await thinking_msg.update()

    except Exception as exc:
        thinking_msg.content = (
            f"❌ Could not reach the FastAPI server at `{FASTAPI_URL}`.\n\n"
            f"Make sure `uvicorn backend.api.main:app --port 8000` is running.\n\n"
            f"Error: {exc}"
        )
        await thinking_msg.update()
