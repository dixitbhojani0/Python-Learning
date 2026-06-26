"""
backend/orchestrator/nodes.py

All LangGraph node functions for the AI SDLC Assistant orchestrator.

Each node takes the current SDLCState and returns a dict of changed fields only.
The graph wiring (which node connects to which) lives in graph.py.

Node list (10 total):
  1.  check_semantic_cache    — Redis semantic cache lookup
  2.  retrieve_memory_context — SQLite session history + LLM summarization
  3.  classify_intent         — LLM supervisor routing (keyword fallback on failure)
  4.  run_cross_source        — CrossSourceAgent (RAG + Jira/Slack/GitHub MCP + LLM)
  5.  run_ticket              — TicketAgent (Jira search + RAG + HITL proposal)
  6.  run_risk                — RiskAgent (Jira MCP + sprint RAG + risk score)
  7.  run_pr_review           — PRReviewAgent (GitHub MCP + version policy RAG + HITL)
  8.  run_release_readiness   — ReleaseReadinessAgent (go/no-go + version policy + HITL)
  9.  check_hitl              — HITL gate (saves proposal, returns Approve/Reject action id)
  10. adapt_persona           — PersonaAdapter rewrites response for user role

Edge routing functions (used in graph.py add_conditional_edges):
  route_cache                 — cache hit → END, miss → retrieve_memory
  route_by_intent             — intent string → agent node name
"""
import asyncio
import logging
import re
from typing import Type

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.agents.cross_source_agent import CrossSourceAgent
from backend.agents.notify_agent import NotifyAgent
from backend.agents.pr_review_agent import PRReviewAgent
from backend.agents.release_readiness_agent import ReleaseReadinessAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.ticket_agent import TicketAgent
from backend.core.config_loader import config
from backend.memory.redis_cache import semantic_cache
from backend.memory.semantic_memory import semantic_memory
from backend.memory.session_store import session_store
from backend.orchestrator.classifier import llm_classify
from backend.orchestrator.hitl import hitl_manager
from backend.orchestrator.rag_helpers import format_rag_context
from backend.orchestrator.state import SDLCState
from backend.persona.adapter import PersonaAdapter
from backend.persona.detector import PersonaDetector
from backend.providers.base_llm import BaseLLMProvider
from backend.providers.factory import LLMFactory
from backend.rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)


# ── Module-level singletons ───────────────────────────────────────────────────
# Created once when nodes.py is first imported (at server startup).
# Shared across all requests — both are stateless and thread-safe.

_retriever: HybridRetriever | None = None
_provider:  BaseLLMProvider | None = None


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def get_provider() -> BaseLLMProvider:
    """Return the singleton LLM provider. Delegates to LLMFactory — provider is config-driven."""
    global _provider
    if _provider is None:
        _provider = LLMFactory.get_provider()
    return _provider


# ── Generic agent runner ──────────────────────────────────────────────────────

def _payload_to_state(payload: AgentPayload) -> dict:
    """Convert a standard AgentPayload into the graph state dict fields."""
    return {
        "final_response": payload.structured.get("final_response", ""),
        "rag_confidence": payload.confidence,
        "rag_chunks":     payload.structured.get("rag_chunks", []),
        "agent_payloads": [payload],
        "hitl_required":  payload.hitl_required,
        "hitl_proposal":  payload.hitl_proposal or {},
        "skip_persona":   payload.structured.get("skip_persona", False),
    }


async def _run_agent(agent_class: Type[BaseAgent], state: SDLCState) -> dict:
    """
    Generic agent runner — shared by all agent nodes.

    Instantiates the agent with shared singletons, calls run(), and converts
    the AgentPayload into graph state fields.
    """
    from backend.mcp.registry import MCPRegistry
    registry = MCPRegistry()

    agent = agent_class(
        retriever=get_retriever(),
        llm=get_provider(),
        mcp_registry=registry,
    )
    payload = await agent.run(state)
    return _payload_to_state(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  NODE 1 — Semantic Cache
# ─────────────────────────────────────────────────────────────────────────────

# ── Live-query patterns — queries whose answers change with every Jira/GitHub call ──
# Matched against lowercased query text. Any match → cache is bypassed so the graph
# always calls live MCP rather than serving a potentially stale cached answer.
_LIVE_QUERY_PATTERNS: list[re.Pattern] = [
    re.compile(p) for p in [
        r'\b(sprint status|sprint progress|sprint health|sprint summary)\b',
        r'\b(open tickets?|in.progress tickets?|active tickets?)\b',
        r'\b(release readiness|release ready|can we release|go.?no.?go)\b',
        r'\b(open pr|pull request|pr status|current pr)\b',
        r'\b(what.?s blocking|what is blocking|blockers?)\b',
        r'sdlc-?\s*\d+',                       # any ticket ID reference (SDLC-5, sdlc 5, sdlc5)
        r'\b(who is (working|assigned)|assigned to|assignee)\b',
        r'\b(this sprint|current sprint|current release)\b',
        r'\b(percentage|percent|completion|how many tickets?)\b',
        r'\b(right now|currently|as of (now|today))\b',
        # Ticket creation/assignment always needs fresh Jira state — never serve from cache
        r'\b(create|log|raise|file|open|report|add)\s+(a\s+)?(ticket|bug|issue|defect|story|task)\b',
        r'\b(assign|reassign)\b',
    ]
]


def _is_live_query(query: str) -> bool:
    """
    Return True if the query asks about current/live state.
    These queries bypass the Redis semantic cache so Jira/GitHub are always called fresh.
    """
    q = query.lower()
    return any(p.search(q) for p in _LIVE_QUERY_PATTERNS)


async def check_semantic_cache(state: SDLCState) -> dict:
    """
    Node 1: Check Redis semantic cache before doing any work.

    Embeds the query and scans Redis for a semantically similar previous response
    (cosine similarity ≥ 0.92). On hit: returns the cached response immediately and
    the conditional edge routes to END — skipping all agents, RAG, and LLM calls.

    On Redis unavailable or any error: SemanticCache degrades gracefully (returns None)
    and we continue as a cache miss. The graph works normally without Redis.

    Cache bypass: queries about current live state (sprint status, open tickets, PRs,
    specific ticket IDs like SDLC-3) always skip the cache. Jira/GitHub data changes
    continuously — a cached "sprint is 60% done" answer becomes stale the moment a
    developer closes a ticket.
    """
    query     = state["query"]
    user_role = state.get("user_role", "")

    if _is_live_query(query):
        logger.info("cache_check: BYPASS (live query) role='%s' query='%s...'", user_role, query[:50])
        return {"response_cached": False}

    cached = await semantic_cache.get_cached(query, user_role)
    if cached:
        logger.info("cache_check: HIT role='%s' query='%s...'", user_role, query[:50])
        return {
            "response_cached": True,
            "final_response":  cached,
        }
    logger.debug("cache_check: MISS role='%s' query='%s...'", user_role, query[:50])
    return {"response_cached": False}


# ─────────────────────────────────────────────────────────────────────────────
#  NODE 2 — Memory Retrieval
# ─────────────────────────────────────────────────────────────────────────────

_RECENT_WINDOW     = 5    # last N turns always passed as raw messages to LLM
_SUMMARY_THRESHOLD = 6    # if session has > this many turns, summarize the older ones


async def _summarize_turns(turns: list[dict]) -> str:
    """
    Compress a list of conversation turns into a ≤250-token summary using the LLM.

    Uses the `conversation_summarizer` prompt from prompts.yaml.
    Returns empty string on any error — the caller treats it as no summary.
    """
    if not turns:
        return ""
    try:
        conversation_text = "\n".join(
            f"User: {t['query']}\nAssistant: {t['response'][:200]}"
            for t in turns
        )
        prompt = config.get_prompt("conversation_summarizer", conversation_text=conversation_text)
        system = config.get_prompt("system_prompt")

        tokens: list[str] = []
        async for chunk in get_provider().generate(prompt, system, temperature=0.1, max_tokens=300):
            tokens.append(chunk)

        summary = "".join(tokens).strip()
        logger.info("retrieve_memory: summarized %d older turns → %d chars", len(turns), len(summary))
        return summary

    except Exception:
        logger.exception("retrieve_memory: summarization failed — returning empty summary")
        return ""


async def retrieve_memory_context(state: SDLCState) -> dict:
    """
    Node 2: Load conversation context from SQLite + summarize long sessions.

    Strategy:
      - Always keep the last _RECENT_WINDOW (5) turns as raw messages
      - If the session has > _SUMMARY_THRESHOLD (6) turns, compress the
        older turns into a single summary using the LLM
      - The summary is placed in slot 3 of the 7-slot context builder
      - Raw recent messages go in slot 4

    This prevents token budget explosion in long sessions while keeping
    enough context for follow-up question handling.
    """
    session_id = state.get("session_id", "")

    all_turns = await session_store.aload_recent_turns(
        session_id,
        limit=_RECENT_WINDOW + _SUMMARY_THRESHOLD,
        response_truncate=0,
    )

    recent_messages: list[dict] = []
    conversation_summary        = ""

    if not all_turns:
        logger.debug("retrieve_memory: session='%s' — no prior history", session_id)
    elif len(all_turns) <= _RECENT_WINDOW:
        recent_messages = [
            {"query": t["query"], "response": t["response"][:300], "role": t["user_role"]}
            for t in all_turns
        ]
        logger.info("retrieve_memory: session='%s' — %d turns (no summary needed)", session_id, len(all_turns))
    else:
        older_turns  = all_turns[:-_RECENT_WINDOW]
        newest_turns = all_turns[-_RECENT_WINDOW:]

        conversation_summary = await _summarize_turns(older_turns)
        recent_messages = [
            {"query": t["query"], "response": t["response"][:300], "role": t["user_role"]}
            for t in newest_turns
        ]
        logger.info(
            "retrieve_memory: session='%s' — %d older turns summarized, %d recent kept raw",
            session_id, len(older_turns), len(newest_turns),
        )

    # Retrieve relevant long-term facts for this query from semantic memory
    query      = state.get("query", "")
    project_id = state.get("project_id", "")
    semantic_facts: list[str] = []
    if query and project_id:
        try:
            semantic_facts = semantic_memory.retrieve_facts(query, project_id)
        except Exception:
            logger.exception("retrieve_memory: semantic_memory lookup failed — continuing without facts")

    return {
        "conversation_summary": conversation_summary,
        "recent_messages":      recent_messages,
        "semantic_context":     semantic_facts,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  NODE 3 — Intent Classification
# ─────────────────────────────────────────────────────────────────────────────

async def classify_intent(state: SDLCState) -> dict:
    """
    Node 3: Determine which agent should handle this query.

    Primary path:  LLM supervisor reads agent routing_descriptions from agents.yaml
                   and picks the right agent with full language understanding.
    Fallback path: keyword matching against trigger_keywords (when LLM fails).
    Fast path:     ticket ID pattern (SDLC-5) → always cross_source, no LLM call.

    Examples with LLM routing:
        "What caused the CORS error?"           → cross_source (investigation, not creation)
        "Create a ticket for the CORS error"    → ticket       (explicit creation intent)
        "What are the blockers in sprint 12?"   → risk         (sprint health)
        "Are we ready to release?"              → release_readiness
        "Review open PRs"                       → pr_review
    """
    query = state["query"]

    # LLM-first routing: the supervisor reads each agent's routing_description from
    # agents.yaml and picks the agent with full language understanding. No regex
    # pre-routing overrides it — llm_classify() internally falls back to keyword
    # matching only if the LLM call fails (rate limit / parse error / unknown agent).
    intent = await llm_classify(query)
    return {
        "intent":        intent,
        "agents_to_run": [intent],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  NODES 4-8 — Agent Runner Nodes
# ─────────────────────────────────────────────────────────────────────────────

async def run_cross_source(state: SDLCState) -> dict:
    """
    Node 4: Cross-Source Correlation Agent.

    Correlates evidence across sprint docs, ADRs, Jira history, and Slack
    using hybrid RAG retrieval + live MCP tool data.
    """
    logger.info("cross_source_agent: invoking CrossSourceAgent")
    return await _run_agent(CrossSourceAgent, state)


async def run_ticket(state: SDLCState) -> dict:
    """
    Node 5: Ticket Creation Agent.

    Searches Jira for similar existing tickets, retrieves past incident RAG context,
    then uses CoT reasoning to propose a well-formed ticket.
    Always requires human approval (hitl_required=True).
    """
    logger.info("ticket_agent: invoking TicketAgent")
    return await _run_agent(TicketAgent, state)


async def run_risk(state: SDLCState) -> dict:
    """
    Node 6: Sprint Risk Detection Agent.

    Fetches live Jira sprint board via MCP, retrieves sprint docs via RAG,
    and uses CoT reasoning to compute a risk score (0–100) with blockers list.
    """
    logger.info("risk_agent: invoking RiskAgent")
    return await _run_agent(RiskAgent, state)


async def run_pr_review(state: SDLCState) -> dict:
    """
    Node 7: PR Review Agent.

    Fetches GitHub PRs, retrieves version policy + coding standards via RAG,
    then proposes a reviewer assignment with HITL approval.
    """
    logger.info("pr_review_agent: invoking PRReviewAgent")
    return await _run_agent(PRReviewAgent, state)


async def run_release_readiness(state: SDLCState) -> dict:
    """
    Node 8: Release Readiness Agent.

    Fetches Jira + GitHub via MCP in parallel, retrieves sprint docs +
    version_policies/ via RAG, and produces a go/no-go JSON verdict.
    Always sets hitl_required=True (release is irreversible).
    """
    logger.info("release_agent: invoking ReleaseReadinessAgent")
    return await _run_agent(ReleaseReadinessAgent, state)


async def run_notify(state: SDLCState) -> dict:
    """
    Node 9b: Notify Agent.

    Parses target channel + message from the user query and sends it
    to Slack via the MCP connector. No HITL — notifications are low-risk.
    """
    logger.info("notify_agent: invoking NotifyAgent")
    return await _run_agent(NotifyAgent, state)


# ─────────────────────────────────────────────────────────────────────────────
#  NODE 9 — HITL Gate
# ─────────────────────────────────────────────────────────────────────────────

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
    """
    if not state.get("hitl_required"):
        return {}

    proposal = state.get("hitl_proposal", {})
    hitl_id  = await hitl_manager.save(
        proposal=proposal,
        context={
            "session_id": state.get("session_id", ""),
            "user_role":  state.get("user_role", ""),
            "project_id": state.get("project_id", ""),
            "query":      state.get("query", ""),
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


# ─────────────────────────────────────────────────────────────────────────────
#  NODE 10 — Persona Adaptation
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_ERROR_SIGNALS = (
    "i'm temporarily unavailable",
    "i could not compute a precise",
    "## release readiness — assessment incomplete",
    "assessment incomplete",
    "temporary system issue",
)

# Short validation messages from agents that must NOT be reworded by the persona layer.
# These are direct factual responses (ticket not found, already assigned, member not found)
# that lose meaning when rewritten into manager/stakeholder language.
_SKIP_PERSONA_SIGNALS = (
    "does not exist in jira",
    "is already assigned to",
    "no action needed",
    "couldn't find a team member named",
    "please check the ticket id",
    "a similar ticket already exists",
    "no new ticket was created",
)


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

    # Do not rewrite HITL proposals — they are structured data, not prose.
    is_no_data = (
        state.get("rag_confidence", 1.0) == 0.0
        and not state.get("rag_chunks", [])
        and not any(
            p.sources
            for p in state.get("agent_payloads", [])
            if hasattr(p, "sources")
        )
    )

    is_system_error  = any(sig in response.lower() for sig in _SYSTEM_ERROR_SIGNALS)
    is_skip_persona  = any(sig in response.lower() for sig in _SKIP_PERSONA_SIGNALS)

    if state.get("hitl_required") or state.get("skip_persona") or not response.strip() or is_no_data or is_system_error or is_skip_persona:
        logger.debug(
            "adapt_persona: skipping — hitl_required=%s, empty=%s, no_data=%s, system_error=%s",
            state.get("hitl_required"), not response.strip(), is_no_data, is_system_error,
        )
        return {}

    detector = PersonaDetector()
    persona  = detector.detect(user_role)

    logger.info("adapt_persona: role='%s' → persona='%s'", user_role, persona)

    adapter = PersonaAdapter(llm=get_provider(), config_loader=config)
    adapted = await adapter.adapt(response, persona)

    logger.info(
        "adapt_persona: %d chars → %d chars (persona='%s')",
        len(response), len(adapted), persona,
    )

    # ── Live evaluation — SYNCHRONOUS so faithfulness/relevancy reach the UI chips.
    # One LLM-judge call per answer (not two): this is the single place faithfulness
    # is computed; the chat route reads these off the returned state.
    query      = state.get("query", "")
    rag_chunks = state.get("rag_chunks", [])
    intent     = state.get("intent", "cross_source")
    project    = state.get("project_id", "")

    faith, relev = 1.0, 0.0   # fail-open defaults (match faithfulness_score's own behavior)
    try:
        import uuid
        import datetime
        from backend.core.metrics import faithfulness_score, answer_relevancy, save_eval_result

        faith = await faithfulness_score(query, adapted, rag_chunks, get_provider())
        relev = answer_relevancy(query, adapted)

        if faith < 0.45:
            logger.warning(
                "eval: LOW FAITHFULNESS faith=%.2f relev=%.2f — response may be hallucinated. "
                "query='%s...' intent='%s'",
                faith, relev, query[:60], intent,
            )
        else:
            logger.info(
                "eval: faithfulness=%.2f relevancy=%.2f intent='%s' query='%s...'",
                faith, relev, intent, query[:50],
            )

        save_eval_result({
            "run_id":           str(uuid.uuid4())[:8],
            "eval_id":          "live",
            "category":         "live_request",
            "intent_expected":  intent,
            "intent_detected":  intent,
            "intent_correct":   True,
            "query":            query,
            "response_snippet": adapted[:200],
            "chunks_retrieved": len(rag_chunks),
            "precision":        0.0,
            "faithfulness":     faith,
            "relevancy":        relev,
            "composite":        round(0.35 * faith + 0.25 * relev, 4),
            "timestamp":        datetime.datetime.now().isoformat(),
            "project":          project,
            "flagged":          faith < 0.45,
        })
    except Exception:
        logger.exception("eval failed — ignoring")

    return {"final_response": adapted, "faithfulness": faith, "relevancy": relev}


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
        "notify":            "notify_agent",
    }
    destination = mapping.get(intent, "cross_source_agent")
    logger.info("route_by_intent: intent='%s' → node='%s'", intent, destination)
    return destination
