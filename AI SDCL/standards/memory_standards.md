# Memory Layer Standards — AI SDLC Assistant

Four memory layers, each with a specific purpose. Using the wrong layer is a common mistake.

---

## The Four Memory Layers

| Layer | Storage | Scope | Lives | Purpose |
|-------|---------|-------|-------|---------|
| 1. Conversational | LangGraph state (in-process) | Last 5-10 messages | Current request only | Follow-up context, pronoun resolution |
| 2. Session | SQLite (SQLAlchemy) | Full session, summarized every 10 turns | Until session expires | User returns next session, context restored |
| 3. Semantic | Qdrant (conversations collection) | Key extracted facts | Persistent | "Auth service blocked on vendor API since May 20" |
| 4. Episodic | SQLite + Qdrant | Ordered event sequences with timestamps | Persistent | What happened in what order, who acted |

**Quick rule**: If you need it for the current response → Layer 1. Current session → Layer 2. Cross-session facts → Layer 3. Ordered history → Layer 4.

---

## Redis — The Hot Cache Layer on Top

Redis does not replace the four layers. It's a fast-access cache in front of them:

| Redis key pattern | What it stores | TTL |
|-------------------|---------------|-----|
| `cache:query:{hash}` | Semantic cache: (embedding, response) pairs | 3600s (1 hour) |
| `hitl:{session_id}:{action_id}` | Paused LangGraph state awaiting approval | 86400s (24 hours) |
| `session:active:{id}` | Active session summary (hot read) | 1800s (30 min) |
| `embed:cache:{hash}` | Pre-computed text embeddings | 604800s (7 days) |
| `stream:response:{id}` | Streaming token buffer for SSE | 300s (5 min) |

Read TTL values from `config/redis.yaml`. Never hardcode TTL values in Python.

**Redis eviction policy is `allkeys-lfu`** (Least Frequently Used), not LRU. LFU keeps frequently-asked queries cached (e.g. Monday morning sprint status query stays cached all week) and evicts queries nobody asks anymore. This is more appropriate than LRU for a semantic cache.

**Hard memory cap**: 256MB set in docker-compose. Redis grows to the cap then evicts per LFU policy. Never remove the `--maxmemory` flag.

```python
# CORRECT
ttl = config.get_redis_config()["ttl"]["semantic_cache"]  # → 3600

# WRONG
redis.setex(key, 3600, value)  # hardcoded TTL
```

---

## Semantic Cache — How it Works

The semantic cache prevents re-calling the LLM for similar queries. It does NOT do exact string match — it does **cosine similarity on embeddings**.

**Check before calling agents**:
```python
query_embedding = embed_text(user_message)
cached = redis_cache.get_cached(query_embedding, threshold=0.92)
if cached:
    return cached   # instant response, zero LLM cost
```

**Store after generating response**:
```python
redis_cache.set_cached(query_embedding, response)
```

Threshold `0.92` means: "only match if the queries are 92%+ semantically similar." Lower threshold = more cache hits but potentially wrong answers. This value comes from `config/redis.yaml`.

---

## Redis Key Naming Rules

Always use the key prefixes defined in `config/redis.yaml`. Never invent your own.

```python
# CORRECT
key = f"hitl:pending:{hitl_id}"
key = f"session:active:{session_id}"

# WRONG — undocumented key schema
key = f"pause_{hitl_id}"
key = f"s:{session_id}"
```

---

## HITL State in Redis + PostgreSQL (Dual-Write)

HITL state is the most critical data in Redis. If Redis is restarted or hits memory pressure and evicts the key, a pending human approval is lost. To prevent this, HITL state is **written to both Redis (fast primary) and PostgreSQL (safety backup)** simultaneously.

```python
async def save_hitl_state(session_id: str, action_id: str, serialized_state: str):
    ttl = config.get_redis_config()["hitl_state"]["ttl_seconds"]   # 86400s (24h)

    # Primary: Redis for fast resume
    await redis_client.setex(
        f"hitl:{session_id}:{action_id}",
        ttl,
        serialized_state,
    )
    # Safety backup: PostgreSQL survives Redis restart
    await session_store.save_hitl_backup(session_id, action_id, serialized_state)

async def load_hitl_state(session_id: str, action_id: str) -> str:
    raw = await redis_client.get(f"hitl:{session_id}:{action_id}")
    if raw:
        return raw
    # Redis expired or was evicted — fall back to PostgreSQL
    return await session_store.load_hitl_backup(session_id, action_id)
```

TTL is 86400s (24 hours) — not 2 hours. Users get a full working day to approve or reject.

---

## Session Memory — SQLite Schema

Session records live in SQLite (dev) or PostgreSQL (prod). Tables:

| Table | Columns | Purpose |
|-------|---------|---------|
| `sessions` | id, user_id, role, project, created_at, last_active_at | Session metadata |
| `messages` | id, session_id, role, content, created_at, thread_id | Raw messages |
| `session_summaries` | id, session_id, summary, turn_count, created_at | Compressed conversation history |

**Conversation summarization rule**: After every 10 messages in a session, compress with `temperature=0.0` LLM call into a 200-300 token summary. Store in `session_summaries`. Raw messages beyond the last 5 are excluded from the context window (but preserved in DB).

---

## Semantic Memory — What Gets Stored

Only extract and store facts that are:
1. Project-specific (not general knowledge)
2. Likely to be relevant in future sessions
3. Not already in the RAG corpus

Examples of what TO store:
- "Auth service blocked on vendor API cert since May 20"
- "CORS on /api/v2/auth resolved by nginx header fix, May 25"
- "Project Antlog team: Alice (Backend), Bob (Frontend), Charlie (DevOps)"

Examples of what NOT to store:
- "User asked what is nginx" — general knowledge, not project-specific
- Sprint doc content — already in RAG corpus
- Raw message text — goes in `messages` table, not semantic memory

---

## Layer 1 — Conversational Memory in LangGraph State

The `messages` field in `SDLCState` is annotated with `add_messages`. This means:
- You write `{"messages": [new_message]}` and LangGraph **appends** it, not overwrites.
- Keep last 5 messages in the context window slot (`_slot_recent` in ContextBuilder).
- Messages older than 5 are still in `state["messages"]` but excluded from the LLM prompt.

---

## What NOT to Store in Redis

- User passwords or raw credentials (never)
- Full document text (Qdrant is for that)
- Conversation history beyond session summary (SQLite is for that)
- Model weights or large binary data (filesystem is for that)

Redis is a short-lived hot cache. If data needs to survive a Redis restart, it must be in SQLite/Qdrant.
