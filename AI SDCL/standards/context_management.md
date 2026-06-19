# Context Management Standards — AI SDLC Assistant

Read this file when working on: `ContextBuilder`, `retrieve_memory` node, any LLM call assembly,
or any code that decides what goes into the prompt.

---

## Section A — The 4 Dimensions of Context

| Dimension | What it is | Where it lives |
|-----------|-----------|----------------|
| **Token budget** | LLM input window — the assembled prompt must fit within the model's max tokens | `ContextBuilder` (tiktoken measurement) |
| **Conversation context** | Prior turns — summary + recent messages carried into current response | Redis (session fast cache) + SQLite (session store) |
| **Graph state context** | Data flowing between the 10 LangGraph nodes during one request | `SDLCState` TypedDict (in-memory during execution) |
| **RAG context** | Retrieved chunks from Qdrant — the evidence the LLM reasons over | Qdrant → `SDLCState.rag_chunks` |

---

## Section B — Token Budget: How It's Measured

Token budget is always measured BEFORE the LLM call using `tiktoken`. Never estimate.

**Rules:**
1. Measure using `tiktoken` with the model's encoding — never use `len(text) / 4` estimates
2. Read max context length from `config/llm.yaml` per model (`context_window`) — never hardcode
3. Reserve 20% of context for the model's response: `available = context_window * 0.8`
4. Allocate remaining budget to the 7 slots in priority order — see Section C

**Example from `llm.yaml`:**
```yaml
primary:
  model: llama-3.3-70b-versatile
  context_window: 128000
  rag_cap: 100000        # max tokens for the full assembled prompt
```

The `rag_cap` is the hard ceiling. `ContextBuilder` enforces this before every LLM call.

---

## Section C — Context Pressure Cascade (What to Cut and In What Order)

When the assembled prompt exceeds budget, compress in this exact order. Never skip steps or reorder.

| Priority | What to cut | Method | Minimum to preserve |
|----------|------------|--------|---------------------|
| 1st | RAG chunks beyond top-3 | Drop lowest reranker score chunks first | Always keep top 3 chunks |
| 2nd | Oldest conversation messages (Slot 4) | Drop oldest-first until within budget | Always keep last 2 messages |
| 3rd | Conversation summary (Slot 3) | Compress to ≤150 tokens via LLM summarizer | Skip slot 3 entirely if < 3 turns |
| 4th | MCP tool outputs (Slot 6) | Drop lowest-priority source: Slack < GitHub < Jira | Keep at least one source |
| **Never cut** | System prompt (Slot 1), Persona (Slot 2), User query (Slot 7) | These are required for correct behavior | Always 100% preserved |

**Rule**: If after all 4 compression steps the prompt still exceeds budget, return a graceful
degradation response (prompt key: `graceful_degradation`). Never truncate mid-sentence.

---

## Section D — Conversation Context Rules

- Summarize conversation when turn count exceeds `summary_threshold` from `config/memory.yaml` (default: 10 turns)
- Summarizer prompt key: `conversation_summarizer` — zero-shot, output capped at 300 tokens
- Summary stored in: Redis (TTL=1800s) AND SQLite (permanent backup)
- Recent messages window: `recent_window` from `config/memory.yaml` (default: last 5 messages)
- Turn 1 (no prior conversation): skip Slot 3 and Slot 4 entirely — never send empty slots

---

## Section E — Graph State Context Rules

These rules apply to all 10 LangGraph nodes:

- Every node returns ONLY the fields it changed — never return a full copy of `SDLCState`
- `agent_payloads` is append-only — each agent appends its `AgentPayload`, never overwrites others
- `rag_chunks` is set once by the retrieval step — agents read it, never write it
- `messages` uses `add_messages` annotator — append only, never replace the full list
- State is never serialized mid-request EXCEPT during HITL pause (Redis via `HITLManager`)

**Node return pattern:**
```python
# Correct — return only changed fields
async def risk_agent_node(state: SDLCState) -> dict:
    payload = await risk_agent.run(state)
    return {
        "agent_payloads": state["agent_payloads"] + [payload],
        "tokens_used": state["tokens_used"] + payload.tokens_used,
    }

# Wrong — never return full state
async def risk_agent_node(state: SDLCState) -> SDLCState:
    ...
    return state   # ← violates LangGraph append semantics
```

---

## Section F — RAG Context Rules

- Child chunk (350 tokens) = embedded and searched — precision
- Parent chunk (1500 tokens) = what the LLM reads — full context
- `rag_confidence` = reranker score of the top chunk → drives confidence tier:
  - HIGH: ≥ 0.75 — answer confidently
  - MEDIUM: 0.45–0.74 — answer with caveat
  - LOW: < 0.45 — trigger corrective RAG (reformulate + retry)
  - NO_EVIDENCE: < 0.20 — skip LLM call, return graceful degradation directly
- After corrective RAG, if confidence still < 0.45: graceful degradation, never hallucinate
- RAG slot token budget per query type: see `query_handling.md` Section E

---

## Section G — Context Anti-Patterns (Never Do These)

| Anti-pattern | Why it's wrong | What to do instead |
|-------------|---------------|-------------------|
| Truncate a chunk mid-sentence to fit | Creates broken, misleading context | Drop the whole chunk; keep the next-best one |
| Keep a low-score chunk to fill space | Lower score = more noise, not more context | Drop it; fewer high-quality chunks > more noisy ones |
| Pass `agent_payloads` list directly to LLM | Raw payloads are structured data, not prose | The `adapt_persona` node synthesizes them into `final_response` |
| Use full conversation history for long sessions | Exceeds context window silently, gets truncated | Summarize at `summary_threshold` turns |
| Estimate tokens with `len(text) / 4` | Wrong for non-English text, wrong for special tokens | Always use `tiktoken` with model-specific encoding |
| Hardcode context window size | Different models have different limits | Always read `context_window` from `config/llm.yaml` |
