"""
backend/orchestrator/nodes.py

All LangGraph node functions for the AI SDLC Assistant orchestrator.

Each node takes the current SDLCState and returns a dict of changed fields only.
The graph wiring (which node connects to which) lives in graph.py.

Node list (10 total):
  1.  check_semantic_cache    — DISABLED no-op (response cache removed, B6)
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
import datetime
import logging
from typing import Type

from langgraph.types import Send

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.agents.cross_source_agent import CrossSourceAgent  # legacy generalist — kept for revert
from backend.agents.mcp_agent import MCPAgent
from backend.agents.notify_agent import NotifyAgent
from backend.agents.pr_review_agent import PRReviewAgent
from backend.agents.release_readiness_agent import ReleaseReadinessAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.ticket_agent import TicketAgent
from backend.core.config_loader import config
from backend.memory.semantic_memory import semantic_memory
from backend.memory.session_store import session_store
from backend.orchestrator.classifier import llm_classify
# from backend.orchestrator.semantic_router import semantic_router  # commented with semantic router
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


_GUARD_SYSTEM = (
    "You are a content moderation assistant. Classify if the user message requests "
    "harmful, illegal, violent, or unethical content. SDLC/software-engineering questions "
    "are always safe. Reply with exactly one word: SAFE or UNSAFE."
)

async def _run_input_guard(query: str) -> tuple[bool, str]:
    """
    C2: LLM-as-judge input content moderation using the primary provider.

    Uses the same llama-3.3-70b model already running — no separate guard model needed.
    Llama Guard 3 was decommissioned on Groq (2025-07); this is the drop-in replacement.

    Returns (is_blocked, reason). Fails open on any error.
    """
    guard_cfg = config.get_llm_config().get("guardrails", {})
    if not guard_cfg.get("enabled", True):
        return False, ""

    try:
        provider = get_provider()
        resp = await provider.generate_text(
            prompt=f"User message: {query}",
            system=_GUARD_SYSTEM,
            temperature=0.0,
            max_tokens=5,
        )
        verdict = (resp.text or "").strip().upper()
        if verdict.startswith("UNSAFE"):
            logger.warning("_run_input_guard: BLOCKED — verdict=%s", verdict)
            return True, verdict
        return False, ""
    except Exception:
        logger.exception("_run_input_guard: guard call failed — allowing query through")
        return False, ""


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

async def check_semantic_cache(state: SDLCState) -> dict:
    """
    Node 1: response cache — DISABLED (B6).

    The Redis answer cache was removed: this assistant is mostly live data
    (Jira/GitHub/Slack via MCP), so caching whole answers served stale results —
    e.g. a closed ticket still shown as a blocker (B1). Cacheability cannot be
    decided from the query text (the old `_LIVE_QUERY_PATTERNS` keyword gate was
    brittle and is exactly the hardcoding P1 forbids).

    Kept as a no-op passthrough so the graph shape is unchanged; every query is
    now answered fresh. Semantic/session/episodic memory are separate and untouched.

    ponytail: cache off entirely. Re-add as a data-driven RAG-only cache (cache
    only answers that used NO live MCP source) if retrieval cost ever justifies it.
    """
    return {"response_cached": False}


# ─────────────────────────────────────────────────────────────────────────────
#  NODE 2 — Memory Retrieval
# ─────────────────────────────────────────────────────────────────────────────

_llm_cfg  = config.get_llm_config()
_mem_cfg  = _llm_cfg.get("memory", {})
_RECENT_WINDOW     = int(_mem_cfg.get("recent_window",     5))   # last N turns kept raw
_SUMMARY_THRESHOLD = int(_mem_cfg.get("summary_threshold", 6))   # older turns get compressed


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
    Node 3: Determine which agent(s) should handle this query.

    Primary path:  LLM supervisor reads agent routing_descriptions from agents.yaml
                   and returns a list of agents — handles compound queries that need
                   multiple specialists (e.g. "create a ticket AND notify the team").
    Fallback path: keyword matching against trigger_keywords (when LLM fails).

    Returns:
        intent:        first agent (kept for backward compat / logging)
        agents_to_run: full list of agents selected by the supervisor

    Examples:
        "What caused the CORS error?"              → ["cross_source"]
        "Create a ticket for the CORS error"       → ["ticket"]
        "Create a ticket AND notify the team"      → ["ticket", "notify"]
        "What are the blockers in sprint 12?"      → ["risk"]
        "Are we ready to release?"                 → ["release_readiness"]
        "Review open PRs"                          → ["pr_review"]
    """
    query = state["query"]

    # ── C2: Llama Guard input content moderation ──────────────────────────────
    is_blocked, _category = await _run_input_guard(query)
    if is_blocked:
        return {
            "intent":         "blocked",
            "agents_to_run":  ["blocked"],
            "final_response": (
                "I can't help with that request. Please ask about sprint docs, "
                "Jira tickets, pull requests, release readiness, or team activity."
            ),
            "skip_persona": True,
        }

    # Semantic router commented out — was designed to save Groq free-tier LLM tokens
    # (~50x cheaper than an LLM call). With GPT-4o, routing cost is negligible and the
    # LLM classifies edge cases (e.g. "release notes content") far more accurately.
    # The router also caches anchors at startup and doesn't pick up YAML changes until
    # a full restart — making prompt tuning painful. Re-enable if token cost matters.
    # intent, score = semantic_router.route(query)
    # if intent is not None:
    #     agents = [intent]
    # else:
    #     agents = await llm_classify(query)
    score  = 0.0
    agents = await llm_classify(query)   # GPT-4o decides all routing

    # Single-agent routing (LangGraph "supervisor" default — the 2026 production
    # norm). We deliberately run ONE agent per query: parallel fan-out would need a
    # fan-in aggregator node + reducer/append-only channels (else concurrent agents
    # collide writing final_response — InvalidUpdateError), and it fights our
    # per-action HITL model (one proposal → approve → execute). A genuinely compound
    # request ("create a ticket AND notify") is SEQUENTIAL — notify needs the ticket
    # that was just created — which parallel Send (same input state to all) cannot
    # express. So take the primary intent; add a sequential supervisor loop later
    # only if a real compound use case appears.
    primary = agents[0] if agents else "cross_source"
    logger.info("classify_intent: query='%s...' → intent='%s' (router top=%.3f)", query[:60], primary, score)
    return {
        "intent":        primary,
        "agents_to_run": [primary],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  NODES 4-8 — Agent Runner Nodes
# ─────────────────────────────────────────────────────────────────────────────

async def run_cross_source(state: SDLCState) -> dict:
    """
    Node 4: generalist agent — RAG + live MCP tool data.

    Now served by MCPAgent (B7 Step 3): the LLM selects + chains real MCP tools
    (gather), and our pipeline synthesizes the answer. Requires the SDLC MCP
    server running; degrades to RAG-only if it's unreachable.

    Legacy CrossSourceAgent is kept (imported below) for one-line revert and
    until its remaining features (image surfacing, duplicate-ticket suggestion)
    are ported — ticket creation returns via the Step-4 write tools + HITL.
    """
    logger.info("run_cross_source: invoking MCPAgent (gather-then-synthesize over MCP)")
    return await _run_agent(MCPAgent, state)


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

    Handles both single-agent and multi-agent responses:
    - Single agent with hitl_required=True: saves one proposal, shows one Approve/Reject.
    - Multiple agents where any requires HITL: saves the HITL proposal and also
      includes non-HITL agent responses in final_response so the user sees all results.

    The frontend sees hitl_action_id in the response and shows Approve/Reject buttons.
    When the user clicks, POST /api/hitl/approve|reject resolves the pending action.
    """
    if not state.get("hitl_required"):
        return {}

    # Find the first HITL proposal across all payloads (ticket_agent or pr_review_agent)
    proposal = state.get("hitl_proposal", {})

    # Also collect non-HITL agent responses to surface alongside the proposal
    non_hitl_responses: list[str] = []
    for p in state.get("agent_payloads", []):
        if hasattr(p, "hitl_required") and not p.hitl_required:
            resp_text = (p.structured or {}).get("final_response", "")
            if resp_text:
                agent_name = getattr(p, "agent_name", "")
                non_hitl_responses.append(
                    f"**{agent_name} result:**\n{resp_text}" if agent_name else resp_text
                )

    hitl_id = await hitl_manager.save(
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
    hitl_text = existing_response if existing_response else _format_hitl_proposal(proposal)

    # If other agents also ran and produced results, show them above the proposal
    if non_hitl_responses:
        combined = "\n\n---\n\n".join(non_hitl_responses) + "\n\n---\n\n" + hitl_text
    else:
        combined = hitl_text

    return {
        "hitl_action_id": hitl_id,
        "final_response": combined,
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


# Reflect only on genuinely low-faith answers. Read from llm.yaml > evaluation.
_REFLECT_FAITH_THRESHOLD: float = float(
    config.get_llm_config().get("evaluation", {}).get("reflect_faithfulness_threshold", 0.45)
)


def _used_mcp(state: SDLCState) -> bool:
    """True if any agent payload sourced live MCP data (sources like 'mcp:<tool>').

    Reflection must skip these: MCP data isn't in rag_chunks, so the judge scores
    it low even when it's correct — regenerating from chunks alone would drop it.
    """
    for p in state.get("agent_payloads", []):
        if any(str(s).startswith("mcp:") for s in getattr(p, "sources", [])):
            return True
    return False


async def _reflect_and_revise(
    query: str, adapted: str, faith: float, rag_chunks: list, persona: str,
) -> tuple[str, float]:
    """Self-critique retry for a low-faithfulness pure-RAG answer.

    Regenerate the answer grounded ONLY in the retrieved evidence, persona-adapt it,
    and re-score. Keep the revision ONLY if faithfulness actually improves — so this
    can never make the answer worse. One retry; degrades to the original on any error.
    """
    evidence = "\n\n".join(
        f"[Chunk {i+1}]: {(c.get('parent_text') or c.get('text') or '')[:500]}"
        for i, c in enumerate(rag_chunks[:6])   # mirror the judge's evidence window
    )
    prompt = config.get_prompt("reflection_retry", query=query, evidence=evidence, draft=adapted[:1200])
    if not prompt:
        return adapted, faith
    system = config.get_prompt("system_prompt")
    try:
        from backend.core.metrics import faithfulness_score

        resp = await get_provider().generate_text(prompt, system, temperature=0.0, max_tokens=600)
        revised_raw = resp.text.strip()
        if not revised_raw:
            return adapted, faith

        revised = await PersonaAdapter(llm=get_provider(), config_loader=config).adapt(revised_raw, persona)
        new_faith = await faithfulness_score(query, revised, rag_chunks, get_provider())

        if new_faith > faith:
            logger.info("reflection: faithfulness %.2f → %.2f — using revised answer", faith, new_faith)
            return revised, new_faith
        logger.info("reflection: revised faith %.2f not better than %.2f — keeping original", new_faith, faith)
    except Exception:
        logger.exception("reflection: retry failed — keeping original answer")
    return adapted, faith


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

    # ── Multi-agent response merge ─────────────────────────────────────────────
    # When multiple agents ran, each wrote its own final_response to agent_payloads.
    # We merge them into one coherent response with section headers before persona
    # adaptation. Single-agent queries are unaffected (payloads list has 1 entry).
    agents_to_run = state.get("agents_to_run", [])
    payloads      = state.get("agent_payloads", [])
    if len(agents_to_run) > 1 and len(payloads) > 1 and not state.get("hitl_required"):
        _AGENT_LABEL = {
            "cross_source": "📋 Research",
            "ticket":       "🎫 Ticket Proposal",
            "risk":         "⚠️ Sprint Risk",
            "pr_review":    "🔍 PR Review",
            "release_readiness": "🚀 Release Readiness",
            "notify":       "📣 Notification",
        }
        sections: list[str] = []
        for idx, payload in enumerate(payloads):
            agent_response = (payload.structured or {}).get("final_response", "")
            if not agent_response:
                continue
            intent = agents_to_run[idx] if idx < len(agents_to_run) else ""
            label  = _AGENT_LABEL.get(intent, f"Agent {idx + 1}")
            sections.append(f"### {label}\n\n{agent_response}")
        if sections:
            response = "\n\n---\n\n".join(sections)
            logger.info(
                "adapt_persona: merged %d agent responses into one (%d chars)",
                len(sections), len(response),
            )

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
    adapted = await adapter.adapt(response, persona, query=state.get("query", ""))

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

        # Reflection loop: a pure-RAG answer that isn't grounded in its evidence
        # gets one self-critique retry. Skipped when MCP data was used (its facts
        # aren't in rag_chunks, so a low score there is a false positive).
        if faith < _REFLECT_FAITH_THRESHOLD and rag_chunks and not _used_mcp(state):
            adapted, faith = await _reflect_and_revise(query, adapted, faith, rag_chunks, persona)
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


# ── Intent → LangGraph node name (derived from agents.yaml) ───────────────────────
# Convention: agent key in agents.yaml IS the node name registered in graph.py.
# e.g. risk_agent → node "risk_agent";  release_readiness_agent → node "release_agent"
# (release_readiness_agent is the one exception — graph.py registers it as "release_agent")
# Exceptions can be listed here explicitly; everything else auto-derives.
_NODE_NAME_OVERRIDES: dict[str, str] = {
    # agent_key               graph node name (only when they differ)
    "release_readiness_agent": "release_agent",
    "pr_agent":                "pr_review_agent",
    "cross_source_agent":      "cross_source_agent",   # same, listed for clarity
}

_cached_intent_to_node: dict[str, str] | None = None


def _build_intent_to_node() -> dict[str, str]:
    """
    Derive {intent: graph_node_name} from agents.yaml via AGENT_INTENT_MAP.

    Convention: node name = agent key, with _NODE_NAME_OVERRIDES for exceptions.
    This means adding a new agent only requires:
      1. A block in agents.yaml
      2. add_node() + add_edge() in graph.py  (LangGraph compile-time requirement)
    Nothing else needs changing here.
    """
    from backend.orchestrator.classifier import get_agent_intent_map
    mapping: dict[str, str] = {}
    for agent_key, intent in get_agent_intent_map().items():
        node = _NODE_NAME_OVERRIDES.get(agent_key, agent_key)
        mapping[intent] = node
    logger.debug("nodes: built _INTENT_TO_NODE from agents.yaml: %s", mapping)
    return mapping


def _get_intent_to_node() -> dict[str, str]:
    global _cached_intent_to_node
    if _cached_intent_to_node is None:
        _cached_intent_to_node = _build_intent_to_node()
    return _cached_intent_to_node


def route_by_intent(state: SDLCState) -> list[Send]:
    """
    After classify_intent: dispatch to the chosen agent node(s) via LangGraph Send.

    classify_intent currently caps agents_to_run to ONE primary intent (single-agent
    supervisor pattern — see its comment), so this returns a single Send, e.g.
        → [Send("risk_agent", state)]
    The Send/list machinery is kept so true multi-agent (with a fan-in aggregator +
    reducer channels) can be reinstated without rewiring the graph.
    """
    # C2: blocked by input guard — skip agents and go straight to persona (which
    # passes through because skip_persona=True is set in the state).
    if state.get("intent") == "blocked":
        return [Send("adapt_persona", state)]

    intent_to_node = _get_intent_to_node()
    agents: list[str] = state.get("agents_to_run", [state.get("intent", "cross_source")])

    sends: list[Send] = []
    for intent in agents:
        node = intent_to_node.get(intent, "cross_source_agent")
        sends.append(Send(node, state))
        logger.info("route_by_intent: intent='%s' → Send('%s')", intent, node)

    if not sends:
        logger.warning("route_by_intent: no agents selected — defaulting to cross_source_agent")
        sends.append(Send("cross_source_agent", state))

    return sends
