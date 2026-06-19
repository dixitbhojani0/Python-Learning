# LangGraph Standards — AI SDLC Assistant

Rules for the LangGraph orchestrator: state design, node functions, edges, HITL, and agent wiring.

---

## State — SDLCState TypedDict

All shared state lives in `backend/orchestrator/state.py`. Never add ad-hoc fields to the state mid-graph.

```python
# backend/orchestrator/state.py
class SDLCState(TypedDict):
    # ── Input (set by FastAPI route before invoking graph)
    messages:             Annotated[list[Any], add_messages]
    session_id:           str
    user_id:              str
    user_role:            str           # developer | tech_lead | manager | stakeholder | admin
    project_id:           str           # used for Qdrant metadata pre-filter
    query:                str           # current user message (convenience copy)
    thread_id:            str           # incident/sprint/feature thread for scoped retrieval

    # ── Orchestrator fills these
    intent:               str           # cross_source | risk | ticket | pr_review | notify | release_readiness
    agents_to_run:        list[str]     # which agents the orchestrator selected for this request
    tokens_budget:        int           # per-request global token budget (for observability)
    tokens_used:          int           # running total across all agents

    # ── Memory (filled by dedicated memory retrieval node)
    conversation_summary: str           # last N turns compressed by LLM
    recent_messages:      list[dict]    # last 5 raw messages
    semantic_context:     list[str]     # retrieved long-term semantic facts

    # ── Agent outputs
    agent_payloads:       list[Any]     # list[AgentPayload] — each agent appends its result
    mcp_outputs:          dict          # normalized MCP results, keyed by source name
    rag_chunks:           list[dict]    # top 5-8 reranked chunks with scores
    rag_confidence:       float         # top reranker score (0.0–1.0)

    # ── HITL gate
    hitl_required:        bool
    hitl_action_id:       str
    hitl_decision:        str | None    # "approve" | "reject" | None (pending)

    # ── Final output
    final_response:       str
    response_cached:      bool
```

**Rule**: If you need a new field, add it to `SDLCState` first. Never pass data outside of state between nodes.

**Important naming note**: The v3 document uses `mcp_outputs` (not `mcp_data`), `rag_chunks` (not `rag_context`), `rag_confidence` (not `confidence_score`), and `hitl_decision` (not `hitl_approved`). Use these exact names everywhere.

---

## Node Function Signature

Every node function follows this exact signature:

```python
async def node_name(state: SDLCState) -> dict:
    """Node docstring: what decision this node makes."""
    # ... logic ...
    return {"field_to_update": new_value}   # return ONLY changed fields
```

**Critical rule**: Return only the fields that changed. LangGraph merges your return dict into the state — you do not need to return the full state.

```python
# CORRECT — return only what changed
async def classify_intent(state: SDLCState) -> dict:
    intent = _classify(state["messages"][-1].content)
    return {"intent": intent}

# WRONG — returning full state causes bugs with add_messages annotator
async def classify_intent(state: SDLCState) -> dict:
    state["intent"] = "cross_source"
    return state   # ← never do this
```

---

## Graph Structure — 10 Nodes

The full graph has 10 nodes. Each node is one step in the pipeline.

```python
# backend/orchestrator/graph.py
from langgraph.graph import StateGraph, END
from backend.orchestrator.state import SDLCState

graph_builder = StateGraph(SDLCState)

# Node 1: Check semantic cache before doing any work
graph_builder.add_node("cache_check",        check_semantic_cache)
# Node 2: Retrieve memory context (conversation summary, recent msgs, semantic facts)
graph_builder.add_node("retrieve_memory",    retrieve_memory_context)
# Node 3: Classify intent and select agents to run
graph_builder.add_node("classify_intent",    classify_intent)
# Node 4-8: Specialist agents (one node per agent)
graph_builder.add_node("cross_source_agent", run_cross_source)
graph_builder.add_node("ticket_agent",       run_ticket)
graph_builder.add_node("risk_agent",         run_risk)
graph_builder.add_node("pr_review_agent",    run_pr_review)
graph_builder.add_node("release_agent",      run_release_readiness)
# Node 9: HITL gate (checks hitl_required and interrupts if needed)
graph_builder.add_node("hitl_gate",          check_hitl)
# Node 10: Persona adaptation (rewrites final_response for user role)
graph_builder.add_node("adapt_persona",      adapt_persona)

graph_builder.set_entry_point("cache_check")
graph_builder.add_edge("retrieve_memory", "classify_intent")
graph_builder.add_edge("hitl_gate", "adapt_persona")
graph_builder.add_edge("adapt_persona", END)

# Cache hit skips everything — return immediately
graph_builder.add_conditional_edges("cache_check", route_cache, {
    "hit":  END,
    "miss": "retrieve_memory",
})

# Intent routes to correct agent
graph_builder.add_conditional_edges("classify_intent", route_by_intent, {
    "cross_source":       "cross_source_agent",
    "ticket":             "ticket_agent",
    "risk":               "risk_agent",
    "pr_review":          "pr_review_agent",
    "release_readiness":  "release_agent",
})

# All agents flow to HITL gate
for agent_node in ["cross_source_agent", "ticket_agent", "risk_agent", "pr_review_agent", "release_agent"]:
    graph_builder.add_edge(agent_node, "hitl_gate")

graph = graph_builder.compile()
```

---

## Conditional Edge Functions

Edge routing functions take state and return the name of the next node as a string.

```python
def route_by_intent(state: SDLCState) -> str:
    intent = state.get("intent", "direct")
    mapping = {
        "cross_source": "cross_source_agent",
        "ticket":       "ticket_agent",
        "risk":         "risk_agent",
        "pr_review":    "pr_agent",
    }
    return mapping.get(intent, "cross_source_agent")   # default to cross_source
```

---

## HITL (Human-in-the-Loop) Pattern

HITL pauses the graph until the user approves or rejects. It does NOT block a thread — it saves state and waits.

```python
# In a HITL gate node:
async def check_hitl(state: SDLCState) -> dict:
    if not state.get("hitl_pending"):
        return {}   # no HITL needed, pass through

    # Save state to Redis before interrupting
    hitl_manager = HITLManager()
    hitl_id = await hitl_manager.pause(state, state["hitl_action"])

    # LangGraph interrupt — suspends execution
    from langgraph.types import interrupt
    interrupt({"hitl_id": hitl_id, "proposal": state["hitl_action"]})

    # Code after interrupt() only runs on resume
    return {}
```

**Resume from FastAPI endpoint**:
```python
# POST /api/hitl/approve
async def approve_hitl(hitl_id: str):
    state = await hitl_manager.resume(hitl_id, approved=True)
    result = await graph.ainvoke(state, config={"thread_id": state["session_id"]})
    return result["final_response"]
```

---

## Agent Auto-Discovery — Class-Level Metadata

Agents self-register via class attributes. The orchestrator scans the `agents/` folder at startup — you never need to edit the orchestrator to add a new agent.

```python
# Every agent class declares these at the class level:
class CrossSourceAgent(BaseAgent):
    name:      str = "cross_source"
    triggers:  list[str] = ["status of", "what is happening", "dashboard", "payment"]
    token_cap: int = 3000   # max tokens this agent can use in one request
```

The `triggers` list maps to `config/agents.yaml`. The `name` is the intent string the orchestrator uses for routing. The `token_cap` is logged to `tokens_used` in state — not enforced in v1, tracked for observability.

**To add a new agent**:
1. Create `backend/agents/my_agent.py` extending `BaseAgent` with class-level `name`, `triggers`, `token_cap`
2. Add to `config/agents.yaml`: `my_agent: enabled: true`
3. Add node to graph: `graph_builder.add_node("my_agent", run_my_agent)`
4. That's it — orchestrator picks it up at next startup

## Agent Nodes — How Agents Connect to the Graph

Agents are not called directly in node functions. Node functions instantiate or retrieve the agent and call `.run(state)`.

```python
# backend/orchestrator/graph.py

async def run_cross_source(state: SDLCState) -> dict:
    agent = CrossSourceAgent(
        mcp_registry=get_mcp_registry(),
        retriever=get_retriever(),
        llm=get_llm_provider(),
        config_loader=config,
    )
    payload: AgentPayload = await agent.run(state)

    return {
        "active_agent":    "cross_source_agent",
        "rag_context":     payload.structured.get("rag_chunks", []),
        "mcp_data":        payload.structured.get("mcp_data", {}),
        "hitl_pending":    payload.hitl_required,
        "hitl_action":     payload.hitl_proposal,
        "confidence_score": payload.confidence,
    }
```

---

## Compiled Graph — Module Level Singleton

Compile the graph once when the module is imported. Never compile per-request (expensive).

```python
# At the bottom of graph.py
graph = graph_builder.compile()
```

Import and use:
```python
from backend.orchestrator.graph import graph

result = await graph.ainvoke(initial_state)
```

---

## State Initialization — FastAPI Route

When a new request comes in, the route creates the initial state dict:

```python
initial_state: SDLCState = {
    "messages":        [HumanMessage(content=request.message)],
    "session_id":      session_id,
    "user_role":       user.role,
    "project":         request.project,
    "intent":          "",
    "active_agent":    "",
    "rag_context":     [],
    "mcp_data":        {},
    "hitl_pending":    False,
    "hitl_action":     {},
    "hitl_approved":   False,
    "final_response":  "",
    "confidence_score": 0.0,
}
```

All fields must be initialized — `SDLCState` has no defaults (TypedDict doesn't provide them).

---

## What NOT to Do in LangGraph

- Never call an external API directly inside a node — use MCP connectors
- Never import the graph inside another graph node (circular)
- Never mutate state in-place: `state["field"] = value` — return the update as a dict
- Never use `graph.invoke()` (synchronous) inside an `async def` — use `graph.ainvoke()`
- Never add logic to the `add_messages` annotator field — it has a fixed merge strategy
