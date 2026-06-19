# Testing Standards — AI SDLC Assistant

What to test, how to test it, which tools to use, and what you are allowed to mock.

---

## Testing Platform

No paid testing platform. Everything runs with:
- **pytest** — test runner
- **pytest-asyncio** — async test support
- **httpx** — async HTTP client for testing FastAPI routes
- Real Docker containers (Qdrant + Redis) for integration tests

Install test dependencies (add to `requirements.txt` dev section):
```
pytest==8.3.5
pytest-asyncio==0.25.2
httpx==0.28.1   # already in requirements.txt
```

---

## Test Directory Structure

```
tests/
  unit/
    test_chunker.py          ← tests for backend/rag/chunker.py
    test_retriever.py        ← tests for backend/rag/retriever.py
    test_context_builder.py  ← tests for backend/core/context_builder.py
    test_config_loader.py    ← tests for backend/core/config_loader.py
    test_persona_adapter.py  ← tests for backend/persona/adapter.py
    test_mcp_registry.py     ← tests for backend/mcp/registry.py
  integration/
    test_rag_pipeline.py     ← full ingest → retrieve cycle (needs Qdrant running)
    test_redis_cache.py      ← cache get/set/expire (needs Redis running)
    test_session_store.py    ← DB save/load session (needs SQLite file)
  smoke/
    test_api_health.py       ← GET /health returns 200 (needs full app running)
    test_chat_flow.py        ← POST /api/chat end-to-end (needs full stack)
  conftest.py                ← shared fixtures (client, mock LLM, test DB)
```

---

## Test Naming Convention

```
test_{what_is_being_tested}_{expected_outcome}

Examples:
  test_chunker_splits_document_into_parent_child_pairs
  test_retriever_returns_empty_on_no_vector_results
  test_context_builder_trims_rag_chunks_when_over_budget
  test_redis_cache_returns_none_on_cache_miss
  test_api_health_returns_200
```

---

## Test Categories and What Each Covers

### Unit Tests — Fast, No Docker Required

Test one function/class in isolation. All external dependencies are mocked.

```python
# tests/unit/test_context_builder.py
import pytest
from backend.core.context_builder import ContextBuilder, count_tokens

def test_count_tokens_returns_nonzero_for_nonempty_string():
    assert count_tokens("Hello world") > 0

def test_count_tokens_returns_zero_for_empty_string():
    assert count_tokens("") == 0

def test_context_builder_assembles_all_slots():
    builder = ContextBuilder()
    prompt = builder.build(
        user_role="developer",
        current_query="What is the nginx issue?",
        rag_chunks=[{"text": "nginx timeout config", "source": "adr", "score": 0.8}],
    )
    assert "nginx" in prompt
    assert "developer" in prompt.lower() or "technical" in prompt.lower()

def test_context_builder_compresses_when_over_budget(monkeypatch):
    # Simulate a huge RAG context
    huge_chunks = [{"text": "a " * 500, "source": "doc", "score": 0.9} for _ in range(10)]
    builder = ContextBuilder()
    prompt = builder.build(user_role="manager", current_query="status?", rag_chunks=huge_chunks)
    from backend.core.context_builder import count_tokens, TOTAL_INPUT_BUDGET
    assert count_tokens(prompt) <= TOTAL_INPUT_BUDGET + 200   # small buffer for assembly overhead
```

### Integration Tests — Require Docker

Test a full layer with real infrastructure. Run only when Docker is up.

```python
# tests/integration/test_rag_pipeline.py
import pytest
from backend.rag.pipeline import RAGPipeline
from backend.rag.retriever import HybridRetriever

@pytest.mark.integration
def test_ingest_and_retrieve():
    """Full cycle: ingest a document, retrieve a relevant chunk."""
    pipeline = RAGPipeline(use_llm_context=False)   # no LLM in integration test
    count = pipeline.ingest_file(
        "data/sprint_docs/sprint_12_plan.md",
        metadata={"project": "test_project", "source": "test", "type": "doc"}
    )
    assert count > 0

    retriever = HybridRetriever()
    chunks, score = retriever.retrieve("dashboard feature blocker", project="test_project")
    assert len(chunks) > 0
    assert score > 0.0
```

Mark integration tests with `@pytest.mark.integration`. Run with:
```bash
pytest tests/integration/ -m integration
```

### Smoke Tests — Require Full Stack

End-to-end tests. Run against a running FastAPI server.

```python
# tests/smoke/test_api_health.py
import pytest
import httpx

@pytest.mark.smoke
def test_health_endpoint():
    with httpx.Client(base_url="http://localhost:8000") as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

---

## Async Tests

Use `pytest-asyncio` for any test involving `async def`.

```python
# In conftest.py
import pytest

@pytest.fixture(scope="session")
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()

# In test files
@pytest.mark.asyncio
async def test_redis_cache_set_and_get():
    from backend.memory.redis_cache import RedisCache
    cache = RedisCache()
    embedding = [0.1] * 384
    await cache.set_cached(embedding, "test response")
    result = await cache.get_cached(embedding, threshold=0.99)
    assert result == "test response"
```

---

## Mocking Rules — What Is Allowed

| What | Unit tests | Integration tests | Smoke tests |
|------|------------|-------------------|-------------|
| Groq LLM calls | ✅ Always mock | ✅ Mock (cost/rate) | ❌ Must be real or skipped |
| Qdrant | ✅ Mock (return fake data) | ❌ Must be real Docker | ❌ Must be real Docker |
| Redis | ✅ Mock (fakeredis) | ❌ Must be real Docker | ❌ Must be real Docker |
| SQLite DB | ✅ Use in-memory `:memory:` | ✅ Use temp file | ❌ Must be real DB |
| MCP connectors | ✅ Always mock | ✅ Use mock connectors | ✅ Mock connectors ok |
| Embedding model | ✅ Return fixed 384-dim vector | ❌ Must be real model | ❌ Must be real model |

**Mock LLM fixture** (put in `conftest.py`):
```python
@pytest.fixture
def mock_llm():
    from unittest.mock import AsyncMock, MagicMock
    llm = MagicMock()
    llm.complete = MagicMock(return_value="Mocked LLM response")
    llm.stream = AsyncMock(return_value=iter(["Mocked ", "response"]))
    return llm
```

---

## Running Tests

```bash
# Unit tests only (fast, no Docker needed)
pytest tests/unit/ -v

# Integration tests (Docker must be running)
pytest tests/integration/ -m integration -v

# Smoke tests (full stack must be running)
pytest tests/smoke/ -m smoke -v

# All tests
pytest tests/ -v

# With coverage
pytest tests/unit/ --cov=backend --cov-report=term-missing
```

---

## Minimum Test Coverage Requirements

These are the must-have tests before calling any phase "done":

| Phase | Minimum tests |
|-------|--------------|
| Phase 1 (RAG) | Unit: chunker, retriever. Integration: ingest + retrieve cycle |
| Phase 2 (LLM provider) | Unit: complete() with mock LLM, error handling |
| Phase 3 (FastAPI) | Smoke: /health, /api/chat with dev token |
| Phase 5 (LangGraph) | Unit: intent classification routing |
| Phase 7 (HITL) | Unit: pause/resume state serialization |
| Phase 9 (Memory) | Integration: Redis cache hit/miss, session save/load |

---

## What NOT to Test

- LangSmith tracing (it's observability, not business logic)
- Config file loading (YAML format errors are developer errors, not test cases)
- Docker health checks (handled by docker-compose)
- UI/frontend rendering (not testable in headless CI)
