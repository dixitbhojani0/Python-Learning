# Resilience Standards — AI SDLC Assistant

Read this file when writing: `GroqProvider`, `MCPRegistry`, any `httpx.AsyncClient` usage,
`asyncio.gather()` calls, or `scheduler.py`.

5 patterns — each with a rule, a code example, and a "never do" counterpart.

---

## Pattern 1 — Groq Rate Limit (429) Backoff

**Where it applies**: `GroqProvider._call_groq()` only. Do NOT apply to agent `run()` methods.

**Library**: `tenacity`

```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

logger = logging.getLogger(__name__)

@retry(
    retry=retry_if_exception_type((RateLimitError, ServiceUnavailableError)),
    wait=wait_exponential(multiplier=1, min=2, max=16),  # 2s → 4s → 8s → 16s cap
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _call_groq(self, messages: list[dict]) -> AsyncIterator[str]:
    ...
```

**Retry on**: HTTP 429 (rate limit), HTTP 503 (service unavailable) — transient, will resolve.

**Do NOT retry on**: HTTP 400, 401, 404, 422 — caller errors that won't fix themselves.

**After 3 failed retries**: raise the exception — the agent catches it and returns a graceful
degradation `AgentPayload`. Never silently swallow the error.

---

## Pattern 2 — Async Gather with Exception Safety

**Where it applies**: Every `asyncio.gather()` call, especially MCP parallel calls.

`return_exceptions=True` is MANDATORY — never omit it. Without it, one failing MCP connector
crashes the entire gather and you lose results from all working connectors.

```python
results = await asyncio.gather(
    jira_connector.search_tickets(query),
    slack_connector.search_messages(query),
    github_connector.get_prs(query),
    return_exceptions=True,              # ← MANDATORY
)

# Filter exceptions — log them but don't crash
valid_results = []
for i, result in enumerate(results):
    if isinstance(result, Exception):
        logger.warning("MCP connector %d failed: %s", i, result)
    else:
        valid_results.append(result)
```

---

## Pattern 3 — MCP Concurrency Cap (Semaphore)

**Where it applies**: `MCPRegistry` when calling multiple connectors simultaneously.

Cap concurrent external calls to avoid overwhelming services. Read the cap from config — never hardcode.

```python
import asyncio
from backend.core.config_loader import config

# Module-level singleton — created once, shared across all requests
_mcp_semaphore: asyncio.Semaphore | None = None

def _get_semaphore() -> asyncio.Semaphore:
    global _mcp_semaphore
    if _mcp_semaphore is None:
        cap = config.get_mcp_config().get("max_concurrent", 3)
        _mcp_semaphore = asyncio.Semaphore(cap)
    return _mcp_semaphore

async def call_with_cap(connector_fn) -> Any:
    async with _get_semaphore():
        return await connector_fn()
```

In `config/mcp_registry.yaml`:
```yaml
max_concurrent: 3    # never run more than 3 MCP calls simultaneously
```

---

## Pattern 4 — httpx Timeout (Always Set)

**Where it applies**: Every `httpx.AsyncClient` instantiation. No exceptions.

Never create an `AsyncClient` without a timeout — the default is no timeout (hangs forever).

```python
import httpx

async with httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=5.0,   # fail fast if service unreachable
        read=25.0,     # Groq streaming can be slow under load
        write=5.0,
        pool=5.0,
    )
) as client:
    response = await client.post(url, json=payload)
    response.raise_for_status()
```

**Read timeout values from config** (`config/llm.yaml`):
```yaml
primary:
  timeout:
    connect: 5
    read: 25
```

---

## Pattern 5 — Scheduler Misfire Handling

**Where it applies**: `config/schedules.yaml` and `backend/core/scheduler.py`.

APScheduler piles up missed jobs if the server was offline. Set `misfire_grace_time` so missed runs
are dropped rather than executed late in a burst.

```yaml
# config/schedules.yaml
schedules:
  sprint_risk_scan:
    agent: risk_agent
    cron: "0 17 * * 1-5"
    misfire_grace_time: 60    # if missed by > 60 seconds, skip — don't run late
    max_instances: 1          # never run 2 copies of the same job simultaneously
    enabled: true
```

`max_instances: 1` prevents the same job from running concurrently if one execution is slow.

---

## Ingestion-Specific: Groq Batch Throttle

During RAG ingestion with `use_llm_context=True`, Groq is called once per chunk.
200 chunks = 200 sequential API calls → rate limit hit in ~1 minute.

**Rule**: Process chunks in batches of 10, sleep 2 seconds between batches.

```python
import asyncio

BATCH_SIZE = 10
BATCH_SLEEP = 2.0   # seconds between batches — keeps throughput ~25 req/min

async def _ingest_with_throttle(self, chunks: list[Chunk]) -> int:
    count = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        for chunk in batch:
            self._embed_and_store(chunk)
            count += 1
        if i + BATCH_SIZE < len(chunks):
            await asyncio.sleep(BATCH_SLEEP)
    return count
```

Read `BATCH_SIZE` and `BATCH_SLEEP` from `config/rag_sources.yaml` in production code — not hardcoded.

---

## Rule Summary Table

| Where | Pattern | Library | Config key |
|-------|---------|---------|------------|
| `GroqProvider._call_groq()` | Exponential backoff on 429/503 | `tenacity` | `llm.yaml: max_retries` |
| Every `asyncio.gather()` | `return_exceptions=True` + filter | `asyncio` | — |
| `MCPRegistry.call_all()` | Semaphore concurrency cap | `asyncio` | `mcp_registry.yaml: max_concurrent` |
| Every `httpx.AsyncClient` | Explicit timeout object | `httpx` | `llm.yaml: timeout` |
| `scheduler.py` | misfire_grace_time + max_instances | `apscheduler` | `schedules.yaml` |
| `RAGPipeline` (LLM mode) | Batch + sleep between batches | `asyncio` | `rag_sources.yaml: batch_size, batch_sleep` |

---

## What NOT to Do

- Do NOT add `@retry` to agent `run()` methods — agents handle failure by returning graceful degradation payloads
- Do NOT use `asyncio.wait_for()` for timeout — set timeout at the `httpx` layer where IO actually happens
- Do NOT retry on HTTP 400/401/404/422 — these are caller errors that won't resolve on retry
- Do NOT omit `return_exceptions=True` from any `asyncio.gather()` — one bad connector must never crash all others
- Do NOT use `time.sleep()` in async code — always `await asyncio.sleep()`
