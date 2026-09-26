"""
tests/unit/test_chat_trace.py

Unit tests for chat.py's _build_trace() — the E9 "why this answer?" assembly.
MCPAgent already computes mcp_calls/rag_chunks per query and classify_intent
now returns routing_reason (see test_classifier_routing_reason.py); this is
the piece that turns graph state into the ChatResponse.trace the UI renders.
Must return None (trace omitted, no empty panel) when there's nothing to
explain: a HITL proposal, or a blocked/no-evidence refusal.
"""
from backend.agents.base_agent import AgentPayload
from backend.api.routes.chat import _build_trace


def _payload(mcp_calls=None, rag_chunks=None) -> AgentPayload:
    structured = {}
    if mcp_calls is not None:
        structured["mcp_calls"] = mcp_calls
    if rag_chunks is not None:
        structured["rag_chunks"] = rag_chunks
    return AgentPayload(agent_name="mcp_agent", summary="", structured=structured)


def _dump(models) -> list[dict]:
    """TraceInfo's list fields are Pydantic models, not dicts — dump for comparison."""
    return [m.model_dump() for m in models]


def test_hitl_proposal_has_no_trace():
    result = {
        "hitl_required": True,
        "routing_reason": "query asks to create a ticket",
        "agent_payloads": [],
        "rag_chunks": [],
    }
    assert _build_trace(result) is None


def test_blocked_or_no_evidence_response_has_no_trace():
    result = {"hitl_required": False, "routing_reason": "", "agent_payloads": [], "rag_chunks": []}
    assert _build_trace(result) is None


def test_full_trace_includes_reason_tools_and_chunks():
    result = {
        "hitl_required": False,
        "routing_reason": "chains Jira + GitHub tools for a cross-system question",
        "agent_payloads": [_payload(
            mcp_calls=[{"tool": "jira_get_blocked_tickets", "args": {}, "error": None}],
        )],
        "rag_chunks": [{"text": "...", "source": "local:Sprint Notes", "score": 0.74}],
    }

    trace = _build_trace(result)

    assert trace.routing_reason == "chains Jira + GitHub tools for a cross-system question"
    assert _dump(trace.tools_called) == [{"tool": "jira_get_blocked_tickets", "ok": True}]
    assert _dump(trace.top_chunks) == [{"source": "local:Sprint Notes", "score": 0.74}]


def test_failed_tool_call_marked_not_ok():
    result = {
        "hitl_required": False,
        "routing_reason": "",
        "agent_payloads": [_payload(
            mcp_calls=[{"tool": "github_list_open_prs", "args": {}, "error": "timeout"}],
        )],
        "rag_chunks": [],
    }

    trace = _build_trace(result)
    assert _dump(trace.tools_called) == [{"tool": "github_list_open_prs", "ok": False}]


def test_duplicate_tool_calls_are_deduped_by_name():
    result = {
        "hitl_required": False,
        "routing_reason": "",
        "agent_payloads": [_payload(
            mcp_calls=[
                {"tool": "jira_get_ticket", "args": {"ticket_id": "SDLC-1"}, "error": None},
                {"tool": "jira_get_ticket", "args": {"ticket_id": "SDLC-2"}, "error": None},
            ],
        )],
        "rag_chunks": [],
    }

    trace = _build_trace(result)
    assert _dump(trace.tools_called) == [{"tool": "jira_get_ticket", "ok": True}]


def test_top_chunks_capped_at_five():
    chunks = [{"source": f"doc-{i}", "score": 1.0 - i * 0.1} for i in range(8)]
    result = {"hitl_required": False, "routing_reason": "", "agent_payloads": [], "rag_chunks": chunks}

    trace = _build_trace(result)
    assert len(trace.top_chunks) == 5
    assert trace.top_chunks[0].model_dump() == {"source": "doc-0", "score": 1.0}


def test_pure_rag_query_has_reason_and_chunks_no_tools():
    result = {
        "hitl_required": False,
        "routing_reason": "document-lookup question, no live-system reference",
        "agent_payloads": [_payload(rag_chunks=[{"text": "...", "source": "local:Checklist", "score": 0.81}])],
        "rag_chunks": [{"text": "...", "source": "local:Checklist", "score": 0.81}],
    }

    trace = _build_trace(result)
    assert trace.tools_called == []
    assert _dump(trace.top_chunks) == [{"source": "local:Checklist", "score": 0.81}]
