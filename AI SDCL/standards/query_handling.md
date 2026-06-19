# Query Handling Standards — AI SDLC Assistant

Read this file when working on: `classify_intent` node, `HybridRetriever`, `ContextBuilder`, or any code
that processes an incoming user query before it reaches the LLM.

This file is the complete decision guide for what happens to a user query from the moment it arrives
to the moment context is assembled for the LLM.

---

## Section A — Query Classification (3 Types)

The first thing the system decides is query type. This drives everything downstream.

| Query Type | Examples | Token length (approx) | Characteristics |
|------------|----------|----------------------|----------------|
| **Simple / Lookup** | "Who owns the auth service?" "What is SDLC-1042?" "Is Redis running?" | < 15 tokens | Single entity, single fact, single source |
| **Medium / Status** | "What is blocking the dashboard feature?" "What is the sprint risk?" "What happened with the nginx issue?" | 15–40 tokens | Multiple sources, 1 agent, clear topic |
| **Complex / Analytical** | "Why did auth keep failing across 3 sprints and what should we change?" "Compare sprint velocity vs current capacity" | > 40 tokens | Multi-agent, cross-session, requires reasoning |

Classification is done by the `classify_intent` node — it sets both `intent` AND infers complexity
from token count + question structure. Sets `retrieval_mode` field in `SDLCState`.

---

## Section B — Embedding Strategy by Query Type

| Query Type | How to embed | Why |
|------------|-------------|-----|
| Simple | Embed as-is: `embed_text(query)` | Short query captures the exact entity well |
| Medium | Embed as-is + extract noun phrases for BM25 | Vector handles semantics; BM25 catches exact ticket IDs, endpoint names |
| Complex | Split into sub-questions, embed each separately, merge results via RRF | One vector can't represent multi-part analytical questions |

**Sub-query decomposition example (Complex):**
```
"Why did auth keep failing across 3 sprints?" →
  ["auth service failures", "sprint 10 auth incidents",
   "sprint 11 auth incidents", "sprint 12 auth incidents", "root cause patterns"]
```

---

## Section C — Retrieval Count by Query Type

Read from `config/rag_sources.yaml` — never hardcode these numbers.

| Query Type | `initial_candidates` | `top_k_after_rerank` | Why |
|------------|---------------------|---------------------|-----|
| Simple | 15 | 3 | One fact needed — more adds noise |
| Medium | 30 | 7 | Standard — covers multiple angles of one topic |
| Complex | 50 | 10–12 | Needs broad coverage; reranker still filters to best |

The `classify_intent` node sets `retrieval_mode: simple | medium | complex` in state.
`HybridRetriever` reads this to select the right counts from config — not via if/else in code.

---

## Section D — Chunk Strategy by Query Type

| Query Type | Return child or parent? | Filter by `type`? |
|------------|------------------------|------------------|
| Simple | Child chunk (350 tokens) is sufficient | Yes — e.g. only `ticket` type for "what is SDLC-1042?" |
| Medium | Parent chunk (1500 tokens) for full context | Optional — filter by topic (e.g. `type: doc` for sprint questions) |
| Complex | Parent chunks from multiple `type` filters | No filter — need cross-source evidence |

---

## Section E — Context Assembly by Query Type (7-Slot ContextBuilder)

| Slot | Simple query | Medium query | Complex query |
|------|-------------|-------------|--------------|
| Slot 1 — System | Always included | Always included | Always included |
| Slot 2 — Persona | Always included | Always included | Always included |
| Slot 3 — Summary | Skip if < 3 turns | Include | Include |
| Slot 4 — Recent messages | Last 2 only | Last 3–5 | Last 5 |
| Slot 5 — RAG context | 3 chunks (~600 tokens) | 7 chunks (~1500 tokens) | 10–12 chunks (~2500 tokens) |
| Slot 6 — Tool outputs | Only relevant source | All relevant sources | All sources |
| Slot 7 — User query | Always full | Always full | Always full |

---

## Section F — End-to-End Flow by Query Type

**Simple query** (e.g. "Who owns the auth service?"):
```
1. Classify → simple, retrieval_mode=simple
2. Check semantic cache (high hit probability for repeated simple queries)
3. Cache miss → embed query as-is
4. Retrieve 15 candidates, rerank to top 3, return child chunks
5. Skip memory retrieval (no conversation context needed for single-fact queries)
6. Assemble mini-prompt: system + persona + RAG (3 chunks) + query
7. LLM call: temperature=0.1, max_tokens=200
8. Cache response
```

**Medium query** (e.g. "What is blocking the dashboard?"):
```
1. Classify → medium, retrieval_mode=medium
2. Check semantic cache
3. Cache miss → embed query + extract noun phrases for BM25
4. Retrieve 30 candidates, rerank to top 7, return parent chunks
5. Retrieve memory: conversation summary + last 5 messages + semantic facts
6. Call MCP: Jira + Slack + GitHub for live data
7. Normalize MCP outputs (LLM compression call)
8. Assemble full 7-slot prompt
9. LLM call: temperature=0.4, max_tokens=1024 (streaming)
10. Cache response
```

**Complex query** (e.g. "Why did auth keep failing across 3 sprints?"):
```
1. Classify → complex, retrieval_mode=complex
2. Semantic cache check (low hit probability — complex queries are unique)
3. Decompose into sub-queries
4. Embed and retrieve for each sub-query (50 candidates each)
5. Merge all results via RRF, rerank to top 12 total
6. Return parent chunks for all 12
7. Full memory retrieval including episodic memory
8. MCP: Jira history, GitHub PR history, Slack full thread search
9. Assemble large context (enforce rag_cap — compress if needed)
10. LLM call: CoT prompt, temperature=0.1, max_tokens=2000
11. Do NOT cache (too specific, unlikely to repeat exactly)
```

---

## Section G — When NOT to Retrieve

Skip the RAG pipeline entirely in these cases:

| Condition | Detection | Action |
|-----------|-----------|--------|
| Greeting / chitchat | "hi", "thanks", "can you help?", "hello" — < 5 tokens, no question mark | Respond with system prompt only, no RAG |
| Semantic cache hit | Cosine similarity > 0.92 against cached embeddings | Return cached response, skip all retrieval |
| MCP resolves it completely | MCP returns definitive current answer (e.g. exact ticket status from Jira) | Use MCP result only, skip RAG — MCP is more authoritative for current state |
| Admin command | "show me the Qdrant collection", "reload config", "list sessions" | Route to admin handler, not RAG |

---

## Section H — Context Pressure at Query Time

If the assembled prompt exceeds the model's token budget, apply the cascade from `context_management.md`.
Quick reference:

1. Drop extra RAG chunks (keep top 3 minimum)
2. Drop oldest conversation messages (keep last 2 minimum)
3. Compress conversation summary to ≤150 tokens
4. Drop lowest-priority MCP source (Slack < GitHub < Jira)
5. If still over: return graceful degradation (prompt key: `graceful_degradation`)
6. Never cut: system prompt, persona, or user query
