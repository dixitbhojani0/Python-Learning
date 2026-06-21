"""
backend/orchestrator/graph.py

LangGraph orchestrator — the central state machine for the AI SDLC Assistant.

Each user request flows through a directed graph of 10 nodes:

  cache_check → retrieve_memory → classify_intent → [agent] → hitl_gate → adapt_persona → END
        ↓ (hit)
        END

Node status in Phase 5a:
  cache_check      — stub: always misses (Redis not wired yet — Phase 9)
  retrieve_memory  — stub: returns empty memory (memory layer — Phase 9)
  classify_intent  — REAL: keyword-based intent routing using agents.yaml triggers
  cross_source     — REAL: full RAG + LLM pipeline (same as Phase 3b chat route)
  ticket_agent     — stub: delegates to cross_source (real agent — Phase 7b)
  risk_agent       — stub: delegates to cross_source (real agent — Phase 10)
  pr_review_agent  — stub: delegates to cross_source (real agent — future)
  release_agent    — stub: delegates to cross_source (real agent — Phase 10)
  hitl_gate        — stub: always pass-through (real HITL — Phase 7a)
  adapt_persona    — stub: pass-through (real persona rewriting — Phase 8)

Why compile graph at module level?
  Compilation is expensive (validates graph structure, builds execution plan).
  Done once at import time — never per-request.
"""
import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END

from backend.agents.base_agent import AgentPayload
from backend.agents.cross_source_agent import CrossSourceAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.release_readiness_agent import ReleaseReadinessAgent
from backend.core.config_loader import config
from backend.mcp.registry import MCPRegistry
from backend.memory.redis_cache import semantic_cache
from backend.memory.session_store import session_store
from backend.orchestrator.hitl import hitl_manager
from backend.orchestrator.state import SDLCState
from backend.persona.adapter import PersonaAdapter
from backend.persona.detector import PersonaDetector
from backend.providers.groq_provider import GroqProvider
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


# ── Module-level singletons ───────────────────────────────────────────────────
# Created once when graph.py is first imported (at server startup).
# Shared across all requests — both are stateless and thread-safe.

_retriever: HybridRetriever | None = None
_provider:  GroqProvider | None    = None
_registry:  MCPRegistry | None     = None


def _get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def _get_provider() -> GroqProvider:
    global _provider
    if _provider is None:
        _provider = GroqProvider()
    return _provider


def _get_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry


# ── Intent classification helpers ─────────────────────────────────────────────

def _keyword_classify(query: str) -> str:
    """
    Classify query intent by matching against trigger keywords in agents.yaml.

    Priority order matters — "blocked" appears in both ticket_agent and risk_agent.
    We check ticket first (specific error reports) then risk (sprint-level blockers)
    then pr_review, then default to cross_source (general status questions).

    Phase 5b will replace this with LLM-based few-shot classification for
    better handling of ambiguous queries.
    """
    query_lower = query.lower()
    agents_cfg  = config.get_agents()

    # Check agents in priority order — first match wins
    priority_order = [
        "ticket_agent",
        "release_readiness_agent",
        "risk_agent",
        "pr_agent",
        "cross_source_agent",
    ]

    intent_map = {
        "cross_source_agent":      "cross_source",
        "risk_agent":              "risk",
        "ticket_agent":            "ticket",
        "pr_agent":                "pr_review",
        "release_readiness_agent": "release_readiness",
    }

    for agent_key in priority_order:
        agent_cfg = agents_cfg.get(agent_key, {})
        if not agent_cfg.get("enabled", False):
            continue
        for keyword in agent_cfg.get("trigger_keywords", []):
            if keyword.lower() in query_lower:
                detected = intent_map.get(agent_key, "cross_source")
                logger.debug(
                    "classify_intent: matched keyword '%s' → intent='%s'",
                    keyword, detected,
                )
                return detected

    logger.debug("classify_intent: no keyword matched → defaulting to 'cross_source'")
    return "cross_source"


# ── RAG + LLM helper — used by cross_source and all stubs ────────────────────

def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a context block for the LLM prompt."""
    if not chunks:
        return ""
    lines = ["## Retrieved Context (Sprint Docs, ADRs, Jira History, Slack)"]
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"\n### Source {i}: {chunk.source} ({chunk.doc_type})")
        lines.append(content)
    return "\n".join(lines)


async def _rag_and_generate(state: SDLCState) -> tuple[str, float, list[RetrievedChunk]]:
    """
    Core RAG + LLM call — shared by cross_source and all stub agents.

    Returns: (response_text, confidence, chunks)
    """
    query      = state["query"]
    project    = state["project_id"]
    user_role  = state["user_role"]

    # ── RAG retrieval
    retriever = _get_retriever()
    chunks, confidence = retriever.retrieve(query, project)

    # ── Build prompt
    system_prompt = config.get_prompt("system_prompt")
    persona       = config.get_prompt(f"persona_{user_role}") or config.get_prompt("persona_developer")
    rag_section   = _format_rag_context(chunks)

    prompt = "\n\n".join(filter(None, [
        persona,
        rag_section,
        f"## Current Question\n{query}",
    ]))

    # ── Call LLM
    provider    = _get_provider()
    temperature = config.get_temperature("response_generation")
    max_tokens  = (
        config.get_llm_config()
        .get("primary", {})
        .get("max_tokens", {})
        .get("response", 1024)
    )

    tokens: list[str] = []
    async for token in provider.generate(prompt, system_prompt, temperature, max_tokens):
        tokens.append(token)

    response_text = "".join(tokens) or "I don't have enough information to answer that confidently."
    return response_text, confidence, chunks


# ─────────────────────────────────────────────────────────────────────────────
#  NODE FUNCTIONS — each takes SDLCState, returns dict of changed fields only
# ─────────────────────────────────────────────────────────────────────────────

async def check_semantic_cache(state: SDLCState) -> dict:
    """
    Node 1: Check Redis semantic cache before doing any work.

    Embeds the query and scans Redis for a semantically similar previous response
    (cosine similarity ≥ 0.92). On hit: returns the cached response immediately and
    the conditional edge routes to END — skipping all agents, RAG, and LLM calls.

    On Redis unavailable or any error: SemanticCache degrades gracefully (returns None)
    and we continue as a cache miss. The graph works normally without Redis.
    """
    query     = state["query"]
    user_role = state.get("user_role", "")
    cached    = await semantic_cache.get_cached(query, user_role)
    if cached:
        logger.info("cache_check: HIT role='%s' query='%s...'", user_role, query[:50])
        return {
            "response_cached": True,
            "final_response":  cached,
        }
    logger.debug("cache_check: MISS role='%s' query='%s...'", user_role, query[:50])
    return {"response_cached": False}


async def retrieve_memory_context(state: SDLCState) -> dict:
    """
    Node 2: Load conversation context from SQLite before classification.

    Loads the last 5 turns for this session so agents can answer follow-up questions
    ("who should fix it?" after "what is blocking dashboard?") without the user
    having to repeat context.

    SQLite call is synchronous — acceptable for a single-user demo.
    Phase 9+: add Qdrant long-term semantic facts and Redis conversation summary.
    """
    session_id = state.get("session_id", "")
    turns      = session_store.load_recent_turns(session_id, limit=5)

    recent_messages = [
        {
            "query":    t["query"],
            "response": t["response"],   # already truncated to 300 chars by SessionStore
            "role":     t["user_role"],
        }
        for t in turns
    ]

    if recent_messages:
        logger.info(
            "retrieve_memory: session='%s' loaded %d prior turns",
            session_id, len(recent_messages),
        )
    else:
        logger.debug("retrieve_memory: session='%s' — no prior history", session_id)

    return {
        "conversation_summary": "",
        "recent_messages":      recent_messages,
        "semantic_context":     [],
    }


async def classify_intent(state: SDLCState) -> dict:
    """
    Node 3: Determine which agent should handle this query.

    Reads trigger keywords from config/agents.yaml.
    Sets state['intent'] which is used by the conditional edge to route.

    Example:
        "What is the sprint risk?" → matches 'sprint status' → intent='risk'
        "CORS error on /api/auth"  → matches 'cors', 'error' → intent='ticket'
        "What is blocking the dashboard?" → matches 'dashboard' → intent='cross_source'
    """
    query  = state["query"]
    intent = _keyword_classify(query)

    logger.info("classify_intent: query='%s...' → intent='%s'", query[:60], intent)
    return {
        "intent":        intent,
        "agents_to_run": [intent],
    }


async def run_cross_source(state: SDLCState) -> dict:
    """
    Node 4: Cross-Source Correlation Agent.

    Phase 5b: delegates to CrossSourceAgent class in backend/agents/.
    This node is now a thin wrapper — instantiate agent, run it, unpack payload to state.

    Phase 6: CrossSourceAgent will also call MCP connectors for live data.
    No changes needed here when that happens — the agent's run() method handles it.
    """
    logger.info("cross_source_agent: invoking CrossSourceAgent")

    agent = CrossSourceAgent(
        retriever=_get_retriever(),
        llm=_get_provider(),
        mcp_registry=_get_registry(),
    )
    payload = await agent.run(state)

    return {
        "final_response": payload.structured.get("final_response", ""),
        "rag_confidence": payload.confidence,
        "rag_chunks":     payload.structured.get("rag_chunks", []),
        "agent_payloads": [payload],
    }


async def run_ticket(state: SDLCState) -> dict:
    """
    Node 5: Ticket Creation Agent — stub with HITL trigger.

    Phase 7b builds the real TicketAgent which first searches Jira for existing
    similar tickets before proposing a new one. For now: generates a ticket
    proposal from RAG context and routes it through the HITL gate for approval.

    Every ticket creation ALWAYS requires human approval (hitl_required=True).
    The proposal goes to check_hitl → user sees Approve/Reject buttons in Chainlit.
    """
    logger.info("ticket_agent: building ticket proposal (stub — real Jira search in Phase 7b)")
    response, confidence, chunks = await _rag_and_generate(state)

    # Build a ticket proposal from the RAG-generated response
    proposal = {
        "action":      "create_ticket",
        "title":       f"Issue: {state['query'][:80]}",
        "description": response[:400],
        "priority":    "MEDIUM",
        "assignee":    "unassigned",
        "project":     state.get("project_id", "antlog"),
    }

    payload = AgentPayload(
        agent_name="ticket_stub",
        confidence=confidence,
        summary=response[:200],
        sources=list({c.source for c in chunks}),
        hitl_required=True,
        hitl_proposal=proposal,
    )
    return {
        "final_response": response,    # check_hitl will overwrite with proposal text
        "rag_confidence": confidence,
        "rag_chunks":     [{"text": c.text, "source": c.source, "score": c.score} for c in chunks],
        "agent_payloads": [payload],
        "hitl_required":  True,
        "hitl_proposal":  proposal,
    }


async def run_risk(state: SDLCState) -> dict:
    """
    Node 6: Sprint Risk Detection Agent — Phase 10 real implementation.

    Delegates to RiskAgent which:
      - Fetches live Jira sprint board via MCP
      - Retrieves sprint docs via RAG
      - Uses CoT reasoning to compute risk score (0–100) with blockers list
    """
    logger.info("risk_agent: invoking RiskAgent")

    agent   = RiskAgent(
        retriever=_get_retriever(),
        llm=_get_provider(),
        mcp_registry=_get_registry(),
    )
    payload = await agent.run(state)

    return {
        "final_response": payload.structured.get("final_response", ""),
        "rag_confidence": payload.confidence,
        "rag_chunks":     payload.structured.get("rag_chunks", []),
        "agent_payloads": [payload],
    }


async def run_pr_review(state: SDLCState) -> dict:
    """
    Node 7: PR Review Agent — stub. Delegates to RAG pipeline."""
    logger.info("pr_review_agent: stub — delegating to RAG pipeline")
    response, confidence, chunks = await _rag_and_generate(state)
    payload = AgentPayload(
        agent_name="pr_review_stub",
        confidence=confidence,
        summary=response[:200],
        sources=list({c.source for c in chunks}),
    )
    return {
        "final_response": response,
        "rag_confidence": confidence,
        "rag_chunks":     [{"text": c.text, "source": c.source, "score": c.score} for c in chunks],
        "agent_payloads": [payload],
    }


async def run_release_readiness(state: SDLCState) -> dict:
    """
    Node 8: Release Readiness Agent — Phase 10 real implementation.

    Delegates to ReleaseReadinessAgent which:
      - Fetches Jira + GitHub via MCP in parallel
      - Retrieves sprint docs + version_policies/ via RAG
      - Produces a go/no-go JSON verdict
      - Always sets hitl_required=True (release is irreversible)
    """
    logger.info("release_agent: invoking ReleaseReadinessAgent")

    agent   = ReleaseReadinessAgent(
        retriever=_get_retriever(),
        llm=_get_provider(),
        mcp_registry=_get_registry(),
    )
    payload = await agent.run(state)

    return {
        "final_response": payload.structured.get("final_response", ""),
        "rag_confidence": payload.confidence,
        "rag_chunks":     payload.structured.get("rag_chunks", []),
        "agent_payloads": [payload],
        "hitl_required":  payload.hitl_required,
        "hitl_proposal":  payload.hitl_proposal,
    }


def _format_hitl_proposal(proposal: dict) -> str:
    """Format a HITL proposal dict into the response text shown to the user."""
    action = proposal.get("action", "unknown action")
    lines  = [f"## 🎫 Action Proposal — *{action}*\n"]

    if action == "create_ticket":
        lines += [
            f"**Title**: {proposal.get('title', 'N/A')}",
            f"**Priority**: {proposal.get('priority', 'MEDIUM')}",
            f"**Assignee**: {proposal.get('assignee', 'unassigned')}",
            f"**Project**: {proposal.get('project', 'N/A')}",
            "",
            "_Review the proposal above and click **Approve** or **Reject**._",
        ]
    else:
        lines.append(str(proposal))

    return "\n".join(lines)


async def check_hitl(state: SDLCState) -> dict:
    """
    Node 9: HITL gate.

    If an agent set hitl_required=True, this node:
      1. Saves the proposal to HITLManager (in-memory, keyed by hitl_id)
      2. Replaces final_response with the formatted proposal text
      3. Sets hitl_action_id so the route can return it to the frontend

    The frontend sees hitl_action_id in the response and shows Approve/Reject buttons.
    When the user clicks, POST /api/hitl/approve|reject resolves the pending action.

    Phase 9 will move HITLManager storage from in-memory to Redis so state
    survives server restarts and is shareable across multiple workers.
    """
    if not state.get("hitl_required"):
        return {}

    proposal = state.get("hitl_proposal", {})
    hitl_id  = hitl_manager.save(
        proposal=proposal,
        context={
            "session_id": state.get("session_id", ""),
            "user_role":  state.get("user_role", ""),
            "project_id": state.get("project_id", ""),
        },
    )

    logger.info("hitl_gate: HITL required — saved as hitl_id='%s'", hitl_id)

    # Use the agent's pre-formatted response if it exists (e.g. ReleaseReadinessAgent
    # already called _format_release_response() and put it in final_response).
    # Only fall back to _format_hitl_proposal() for stubs that produce no formatted text.
    existing_response = state.get("final_response", "").strip()
    final_response    = existing_response if existing_response else _format_hitl_proposal(proposal)

    return {
        "hitl_action_id": hitl_id,
        "final_response": final_response,
    }


async def adapt_persona(state: SDLCState) -> dict:
    """
    Node 10: Persona adaptation.

    Takes the agent's final_response and rewrites it in role-appropriate language
    via a second LLM call focused solely on communication style.

    Developer:   keep technical detail (error codes, endpoints, ticket IDs)
    Manager:     delivery language (completion %, blocker owner, ETA, sprint risk)
    Stakeholder: plain English (zero technical terms, business impact only)

    Skipped when hitl_required=True — HITL proposals must be precise, not paraphrased.
    Falls back to original response on any LLM failure.
    """
    response  = state.get("final_response", "")
    user_role = state.get("user_role", "developer")

    # Do not rewrite HITL proposals — they are structured data, not prose
    if state.get("hitl_required") or not response.strip():
        logger.debug("adapt_persona: skipping (hitl_required or empty response)")
        return {}

    detector = PersonaDetector()
    persona  = detector.detect(user_role)

    logger.info("adapt_persona: role='%s' → persona='%s'", user_role, persona)

    adapter = PersonaAdapter(llm=_get_provider(), config_loader=config)
    adapted = await adapter.adapt(response, persona)

    logger.info(
        "adapt_persona: %d chars → %d chars (persona='%s')",
        len(response), len(adapted), persona,
    )
    return {"final_response": adapted}


# ─────────────────────────────────────────────────────────────────────────────
#  EDGE ROUTING FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def route_cache(state: SDLCState) -> str:
    """
    After cache_check: if it was a cache hit, return immediately.
    Otherwise continue to memory retrieval.
    """
    if state.get("response_cached"):
        logger.info("route_cache: cache HIT — returning early")
        return "hit"
    return "miss"


def route_by_intent(state: SDLCState) -> str:
    """
    After classify_intent: route to the correct agent node.
    Maps intent string to agent node name.
    Default to cross_source if intent is unknown.
    """
    intent = state.get("intent", "cross_source")
    mapping = {
        "cross_source":      "cross_source_agent",
        "ticket":            "ticket_agent",
        "risk":              "risk_agent",
        "pr_review":         "pr_review_agent",
        "release_readiness": "release_agent",
    }
    destination = mapping.get(intent, "cross_source_agent")
    logger.info("route_by_intent: intent='%s' → node='%s'", intent, destination)
    return destination


# ─────────────────────────────────────────────────────────────────────────────
#  GRAPH CONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

_builder = StateGraph(SDLCState)

# ── Register all nodes (function name → node identifier in the graph)
_builder.add_node("cache_check",        check_semantic_cache)
_builder.add_node("retrieve_memory",    retrieve_memory_context)
_builder.add_node("classify_intent",    classify_intent)
_builder.add_node("cross_source_agent", run_cross_source)
_builder.add_node("ticket_agent",       run_ticket)
_builder.add_node("risk_agent",         run_risk)
_builder.add_node("pr_review_agent",    run_pr_review)
_builder.add_node("release_agent",      run_release_readiness)
_builder.add_node("hitl_gate",          check_hitl)
_builder.add_node("adapt_persona",      adapt_persona)

# ── Set entry point
_builder.set_entry_point("cache_check")

# ── Cache check: hit → END immediately, miss → continue to memory
_builder.add_conditional_edges(
    "cache_check",
    route_cache,
    {"hit": END, "miss": "retrieve_memory"},
)

# ── Fixed edges (always go to next step)
_builder.add_edge("retrieve_memory",  "classify_intent")
_builder.add_edge("hitl_gate",        "adapt_persona")
_builder.add_edge("adapt_persona",    END)

# ── Intent routing: classify_intent → correct agent
_builder.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    {
        "cross_source_agent": "cross_source_agent",
        "ticket_agent":       "ticket_agent",
        "risk_agent":         "risk_agent",
        "pr_review_agent":    "pr_review_agent",
        "release_agent":      "release_agent",
    },
)

# ── All agent nodes flow to hitl_gate
for _agent_node in ["cross_source_agent", "ticket_agent", "risk_agent", "pr_review_agent", "release_agent"]:
    _builder.add_edge(_agent_node, "hitl_gate")

# ── Compile — validates graph structure and builds execution plan
# Done once at module import time — never per-request
graph = _builder.compile()

logger.info("LangGraph orchestrator compiled — %d nodes ready", 10)
