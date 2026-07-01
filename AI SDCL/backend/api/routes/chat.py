"""
backend/api/routes/chat.py

POST /api/chat — the main chat endpoint.

Flow (Phase 5a — via LangGraph orchestrator):
  1. Auth: validate x-token header → get UserContext (role, project)
  2. Sanitize user message (prompt injection protection)
  3. Build initial SDLCState with all required fields
  4. Invoke LangGraph graph — it handles: cache check, memory, classification, agent, persona
  5. Extract final_response and metadata from graph result
  6. Return ChatResponse

The route no longer calls RAG or LLM directly.
All intelligence lives in the graph — this route is just the HTTP boundary.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from langchain_core.messages import HumanMessage

from backend.api.limiter import limiter
from backend.api.models.schemas import ChatRequest, ChatResponse
from backend.auth.middleware import UserContext, get_current_user
from backend.core.prompt_safety import safety_guard
from backend.memory.semantic_memory import semantic_memory
from backend.memory.session_store import session_store
from backend.orchestrator.graph import graph
from backend.providers.groq_provider import set_stream_id, write_stream_done

logger = logging.getLogger(__name__)
router = APIRouter()


@limiter.limit("10/minute")
@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    Main chat endpoint — delegates everything to the LangGraph orchestrator.

    Auth: send header  x-token: dev_token_alice

    Example:
        curl -X POST http://localhost:8000/api/chat
          -H "x-token: dev_token_alice"
          -H "Content-Type: application/json"
          -d '{"message": "What is blocking the dashboard?", "project": "SDLC"}'
    """
    project    = body.project or user.project
    session_id = body.session_id or str(uuid.uuid4())
    stream_id  = str(uuid.uuid4())

    # ── Security gate — BEFORE any MCP call, graph execution, or RAG ────────
    #
    # All detect_injection()=True cases are hard-blocked here.
    # Sanitise-and-continue is NOT used because a sanitised injection query
    # can still cause the LLM to disclose internal config or hallucinate
    # sensitive information (observed: template injection → API key paths exposed).
    if safety_guard.detect_injection(body.message):
        logger.warning(
            "Chat: injection BLOCKED — user='%s' role='%s' query='%s'",
            user.name, user.role, body.message[:120].replace("\n", "\\n"),
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=403,
            content={
                "response": (
                    "Your message contained a pattern that looks like a prompt injection "
                    "attempt and has been blocked. Please ask a software development question."
                ),
                "confidence": 0.0,
                "sources": [],
                "session_id": session_id,
                "stream_id": stream_id,
                "strategy": "blocked",
                "hitl_required": False,
                "hitl_action_id": None,
                "response_cached": False,
            },
        )

    # Sanitize residual braces / null bytes in benign inputs AFTER the hard block.
    safe_message = safety_guard.sanitize(body.message)

    logger.info(
        "Chat: user='%s' role='%s' project='%s' query='%s'",
        user.name, user.role, project, body.message[:80],
    )

    # ── Build initial graph state ─────────────────────────────────────────────
    #
    # All SDLCState fields must be initialized — TypedDict has no defaults.
    # The graph nodes will populate most of these as they run:
    #   classify_intent  fills → intent, agents_to_run
    #   retrieve_memory  fills → conversation_summary, recent_messages, semantic_context
    #   agent nodes      fill  → final_response, rag_confidence, rag_chunks, agent_payloads

    initial_state = {
        # ── Request inputs — use safe_message for LLM; body.message preserved below
        "messages":             [HumanMessage(content=safe_message)],
        "session_id":           session_id,
        "user_id":              user.name,
        "user_role":            user.role,
        "project_id":           project,
        "query":                safe_message,
        "thread_id":            "",

        # ── Orchestrator fields — filled by graph nodes
        "intent":               "",
        "agents_to_run":        [],
        "tokens_budget":        8000,
        "tokens_used":          0,

        # ── Memory — filled by retrieve_memory node (stub: empty for now)
        "conversation_summary": "",
        "recent_messages":      [],
        "semantic_context":     [],

        # ── Agent outputs — filled by agent nodes
        "agent_payloads":       [],
        "mcp_outputs":          {},
        "rag_chunks":           [],
        "rag_confidence":       0.0,

        # ── HITL — filled by hitl_gate node
        "hitl_required":        False,
        "hitl_action_id":       "",
        "hitl_proposal":        {},
        "hitl_decision":        None,

        # ── Final output — filled by agent + adapt_persona nodes
        "final_response":       "",
        "response_cached":      False,
    }

    # ── Invoke the graph ──────────────────────────────────────────────────────
    #
    # Enable SSE token streaming: set the context var so GroqProvider.generate()
    # pushes each token to Redis list stream:{stream_id}. The /api/stream endpoint
    # polls that list and forwards tokens to the browser as Server-Sent Events.
    set_stream_id(stream_id)
    try:
        result = await graph.ainvoke(initial_state)
    finally:
        set_stream_id("")   # always reset — prevents leaking into unrelated requests

    # ── Build response ────────────────────────────────────────────────────────
    response_text = result.get("final_response") or (
        "I don't have enough information to answer that confidently. "
        "Please try rephrasing your question."
    )

    # Signal the SSE endpoint that the response is complete.
    # The final_response text is stored as the done sentinel value so the
    # Angular frontend can display it even if it missed the token stream.
    await write_stream_done(stream_id, response_text)

    # ── Persist conversation turn to SQLite (always — even on cache hits)
    # Use body.message (original) so the audit trail records what the user typed,
    # not the sanitized version.
    # When HITL is pending the response_text is the proposal card (e.g. "Create ticket…").
    # Storing that verbatim would pollute the session log with action metadata instead of
    # a real answer. Save a clean placeholder; the real outcome is recorded after approve/reject.
    _saved_response = (
        "[Action proposal pending approval]"
        if result.get("hitl_required")
        else response_text
    )
    await session_store.asave_turn(
        session_id=session_id,
        user_id=user.name,
        user_role=user.role,
        query=body.message,
        response=_saved_response,
        project_id=project,
    )

    # ── Extract and store long-term facts in semantic memory (Qdrant)
    # Skipped for:
    #   - cache hits        (no new information to extract)
    #   - HITL proposals    (not a final answer — proposal text is not a fact)
    #   - fallback answers  (LLM said "I don't know" — not worth storing as a fact)
    #   - low confidence    (< 0.15 — source evidence too weak to trust)
    if (
        not result.get("response_cached")
        and not result.get("hitl_required")
        and not response_text.startswith("I don't have enough information")
        and result.get("rag_confidence", 0.0) >= 0.15
    ):
        try:
            stored = await semantic_memory.extract_and_save(
                query=body.message,
                response=response_text,
                project_id=project,
                session_id=session_id,
                user_id=user.name,
            )
            if stored:
                logger.info("Chat: stored %d new semantic facts for project='%s'", stored, project)
        except Exception:
            logger.exception("Chat: semantic_memory.extract_and_save failed — continuing")

    # Response cache removed (B6) — this assistant is mostly live MCP data, so caching
    # whole answers served stale results. Every query is now answered fresh.

    # Collect unique source names from RAG chunks + agent payloads (includes MCP sources)
    rag_sources   = {chunk.get("source", "unknown") for chunk in result.get("rag_chunks", [])}
    agent_sources = {
        src
        for payload in result.get("agent_payloads", [])
        for src in getattr(payload, "sources", [])
    }
    sources = list(rag_sources | agent_sources)

    # Extract intent for logging (and future response metadata)
    detected_intent = result.get("intent", "cross_source")
    confidence      = result.get("rag_confidence", 0.0)

    logger.info(
        "Chat: intent='%s' confidence=%.3f sources=%s",
        detected_intent, confidence, sources,
    )

    hitl_action_id = result.get("hitl_action_id", "") or None
    hitl_required  = bool(hitl_action_id)

    # Real RAG strategy from the agent payload (first_pass / corrective / full_document /
    # degraded) so the UI can show "corrective RAG executed" etc. without log-diving.
    agent_strategy = ""
    for payload in result.get("agent_payloads", []):
        s = getattr(payload, "structured", {}).get("rag_strategy")
        if s:
            agent_strategy = s
            break
    if result.get("response_cached"):
        strategy = "cached"
    elif hitl_required:
        strategy = "hitl_pending"
    else:
        strategy = agent_strategy or "first_pass"

    # Live eval metrics computed once in the adapt_persona node (synchronous) and
    # returned on the graph state — read them here for the UI chips. Empty (0.0) for
    # HITL cards / cache hits / skipped-persona answers.
    faithfulness = float(result.get("faithfulness", 0.0) or 0.0)
    relevancy    = float(result.get("relevancy", 0.0) or 0.0)

    # Collect document images surfaced by agents (dedupe by url, preserve order).
    images: list[dict] = []
    _seen_img: set[str] = set()
    for payload in result.get("agent_payloads", []):
        for img in getattr(payload, "structured", {}).get("images", []):
            url = img.get("url", "")
            if url and url not in _seen_img:
                _seen_img.add(url)
                images.append(img)

    return ChatResponse(
        response=response_text,
        confidence=round(confidence, 3),
        sources=sources,
        session_id=session_id,
        stream_id=stream_id,
        strategy=strategy,
        agent=detected_intent,
        relevancy=round(relevancy, 3),
        faithfulness=round(faithfulness, 3),
        hitl_required=hitl_required,
        hitl_action_id=hitl_action_id,
        response_cached=bool(result.get("response_cached")),
        images=images,
    )
