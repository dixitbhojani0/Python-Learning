"""
backend/orchestrator/state.py

Defines the shared state schema for the LangGraph orchestrator.
Every agent reads from and writes to this state during execution.
"""
from typing import TypedDict, Annotated, Any
from langgraph.graph.message import add_messages


class SDLCState(TypedDict):
    """
    Shared state of the SDLC assistant graph.
    All 10 graph nodes read from and write to this state.
    Each node returns only the fields it changed — LangGraph merges.
    """
    # ── Input — set by FastAPI route before invoking the graph
    messages:             Annotated[list[Any], add_messages]  # use add_messages — never overwrite
    session_id:           str       # unique thread identifier
    user_id:              str       # authenticated user identifier
    user_role:            str       # developer | tech_lead | manager | stakeholder | admin
    project_id:           str       # used for Qdrant metadata pre-filter
    query:                str       # current user message (convenience copy from messages[-1])
    thread_id:            str       # incident/sprint/feature thread for scoped memory retrieval

    # ── Orchestrator fills these (classify_intent node)
    intent:               str       # cross_source | risk | ticket | pr_review | release_readiness | notify
    agents_to_run:        list[str] # which agents were selected for this request
    tokens_budget:        int       # per-request token budget (for observability, not enforcement in v1)
    tokens_used:          int       # running total across all agents in this request

    # ── Memory retrieval node fills these
    conversation_summary: str       # last N turns compressed by LLM
    recent_messages:      list[dict] # last 5 raw messages from session store
    semantic_context:     list[str]  # retrieved long-term semantic facts from Qdrant

    # ── Agent outputs — each agent appends its AgentPayload
    agent_payloads:       list[Any]  # list[AgentPayload] — grow via append, never overwrite
    mcp_outputs:          dict       # normalized MCP results, keyed by source ("jira", "slack", etc.)
    rag_chunks:           list[dict] # top 5-8 reranked chunks with scores
    rag_confidence:       float      # top reranker score (0.0–1.0) — drives confidence tier

    # ── HITL gate
    hitl_required:        bool
    hitl_action_id:       str
    hitl_proposal:        dict        # proposal set by agent, read by check_hitl node
    hitl_decision:        str | None  # "approve" | "reject" | None (pending)

    # ── Final output
    final_response:       str
    response_cached:      bool        # True if this response was served from semantic cache
