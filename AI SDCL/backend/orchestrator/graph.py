"""
backend/orchestrator/graph.py

LangGraph orchestrator — the central state machine for the AI SDLC Assistant.

Each user request flows through a directed graph of 10 nodes:

  cache_check → retrieve_memory → classify_intent → [agent] → hitl_gate → adapt_persona → END
        ↓ (hit)
        END

This file is responsible for one thing only:
  Building, wiring, and compiling the LangGraph graph.

All node implementations live in nodes.py.
All intent classification logic lives in classifier.py.
All RAG + LLM shared helpers live in rag_helpers.py.

Why compile graph at module level?
  Compilation is expensive (validates graph structure, builds execution plan).
  Done once at import time — never per-request.
"""
import logging

from langgraph.graph import StateGraph, END

from backend.orchestrator.nodes import (
    # Node functions
    check_semantic_cache,
    retrieve_memory_context,
    classify_intent,
    run_cross_source,
    run_ticket,
    run_risk,
    run_pr_review,
    run_release_readiness,
    run_notify,
    check_hitl,
    adapt_persona,
    # Edge routing
    route_cache,
    route_by_intent,
)
from backend.orchestrator.state import SDLCState

logger = logging.getLogger(__name__)


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
_builder.add_node("notify_agent",       run_notify)
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
        "notify_agent":       "notify_agent",
    },
)

# ── All agent nodes flow to hitl_gate
for _agent_node in ["cross_source_agent", "ticket_agent", "risk_agent", "pr_review_agent", "release_agent", "notify_agent"]:
    _builder.add_edge(_agent_node, "hitl_gate")

# ── Compile — validates graph structure and builds execution plan
# Done once at module import time — never per-request
graph = _builder.compile()

logger.info("LangGraph orchestrator compiled — 10 nodes ready")
