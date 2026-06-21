"""
backend/api/routes/chat.py

POST /api/chat — the main chat endpoint.

Flow (Phase 5a — via LangGraph orchestrator):
  1. Auth: validate x-token header → get UserContext (role, project)
  2. Build initial SDLCState with all required fields
  3. Invoke LangGraph graph — it handles: cache check, memory, classification, agent, persona
  4. Extract final_response and metadata from graph result
  5. Return ChatResponse

The route no longer calls RAG or LLM directly.
All intelligence lives in the graph — this route is now just the HTTP boundary.
"""
import logging
import uuid

from fastapi import APIRouter, Depends
from langchain_core.messages import HumanMessage

from backend.api.models.schemas import ChatRequest, ChatResponse
from backend.auth.middleware import UserContext, get_current_user
from backend.memory.redis_cache import semantic_cache
from backend.memory.session_store import session_store
from backend.orchestrator.graph import graph

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    Main chat endpoint — delegates everything to the LangGraph orchestrator.

    Auth: send header  x-token: dev_token_alice

    Example:
        curl -X POST http://localhost:8000/api/chat
          -H "x-token: dev_token_alice"
          -H "Content-Type: application/json"
          -d '{"message": "What is blocking the dashboard?", "project": "antlog"}'
    """
    project    = request.project or user.project
    session_id = request.session_id or str(uuid.uuid4())

    logger.info(
        "Chat: user='%s' role='%s' project='%s' query='%s'",
        user.name, user.role, project, request.message[:80],
    )

    # ── Build initial graph state ─────────────────────────────────────────────
    #
    # All SDLCState fields must be initialized — TypedDict has no defaults.
    # The graph nodes will populate most of these as they run:
    #   classify_intent  fills → intent, agents_to_run
    #   retrieve_memory  fills → conversation_summary, recent_messages, semantic_context
    #   agent nodes      fill  → final_response, rag_confidence, rag_chunks, agent_payloads

    initial_state = {
        # ── Request inputs
        "messages":             [HumanMessage(content=request.message)],
        "session_id":           session_id,
        "user_id":              user.name,
        "user_role":            user.role,
        "project_id":           project,
        "query":                request.message,
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
    # graph.ainvoke() runs the full graph asynchronously:
    #   cache_check → retrieve_memory → classify_intent → [agent] → hitl_gate → adapt_persona
    #
    # It returns the final state dict after all nodes have run.
    # We extract what we need for the HTTP response.

    result = await graph.ainvoke(initial_state)

    # ── Build response ────────────────────────────────────────────────────────
    response_text = result.get("final_response") or (
        "I don't have enough information to answer that confidently. "
        "Please try rephrasing your question."
    )

    # ── Persist conversation turn to SQLite (always — even on cache hits)
    # Saved BEFORE caching so the user's question is recorded regardless.
    # HITL proposals are also saved so the user can see what was proposed.
    session_store.save_turn(
        session_id=session_id,
        user_id=user.name,
        user_role=user.role,
        query=request.message,
        response=response_text,
    )

    # ── Cache the response in Redis (only fresh answers, not cache hits or HITL)
    # response_cached=True means the graph already read this from cache — don't re-store.
    # hitl_required=True means the response is a proposal, not a final answer — don't cache it.
    if not result.get("response_cached") and not result.get("hitl_required"):
        await semantic_cache.set_cached(request.message, response_text, user_role=user.role)

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

    return ChatResponse(
        response=response_text,
        confidence=round(confidence, 3),
        sources=sources,
        session_id=session_id,
        strategy="hitl_pending" if hitl_required else "first_pass",
        hitl_required=hitl_required,
        hitl_action_id=hitl_action_id,
        response_cached=bool(result.get("response_cached")),
    )
