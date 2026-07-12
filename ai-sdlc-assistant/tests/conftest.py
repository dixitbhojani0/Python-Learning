"""
tests/conftest.py — Shared pytest fixtures for all test categories.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Mock LLM provider ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """
    Fake BaseLLMProvider that returns a fixed answer without calling Groq.
    Use for unit tests that need an LLM but must not hit the real API.
    """
    llm = MagicMock()

    async def _fake_generate(prompt, system, temperature, max_tokens):
        for word in ["This ", "is ", "a ", "mocked ", "response."]:
            yield word

    llm.generate = _fake_generate

    async def _fake_generate_text(prompt, system, temperature, max_tokens):
        from backend.providers.llm_response import LLMResponse
        return LLMResponse(text="Mocked LLM response.", model="mock", is_empty=False)

    llm.generate_text = AsyncMock(side_effect=_fake_generate_text)

    async def _fake_generate_structured(prompt, system, temperature, max_tokens):
        from backend.providers.llm_response import LLMResponse
        return LLMResponse(
            text='{"risk_level": "LOW", "risk_score": 10}',
            model="mock",
            structured={"risk_level": "LOW", "risk_score": 10},
            is_empty=False,
            parse_error=False,
        )

    llm.generate_structured = AsyncMock(side_effect=_fake_generate_structured)
    llm.get_model_name = MagicMock(return_value="mock-model")
    llm.get_model_window = MagicMock(return_value=8192)
    return llm


# ── Fixed 384-dim embedding ───────────────────────────────────────────────────

@pytest.fixture
def fake_embedding():
    """384-dim unit vector — use to avoid loading sentence-transformers in unit tests."""
    vec = [0.0] * 384
    vec[0] = 1.0
    return vec


# ── Mock Qdrant vector store ──────────────────────────────────────────────────

@pytest.fixture
def mock_vector_store():
    vs = MagicMock()
    vs.search.return_value = [
        {
            "id":          "chunk-001",
            "score":       0.88,
            "text":        "Sprint 12 velocity was 34 story points.",
            "parent_text": "Sprint 12 velocity was 34 story points. The team completed 8 of 10 planned tickets.",
            "source":      "local",
            "type":        "sprint_notes",
            "metadata":    {"project": "SDLC"},
        }
    ]
    vs.upsert.return_value = "chunk-001"
    vs.count.return_value = 1
    return vs


# ── Mock MCP registry ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_mcp():
    registry = MagicMock()

    jira = MagicMock()
    jira.search_tickets = AsyncMock(return_value=[
        {"id": "SDLC-1", "title": "Login page timeout", "status": "Open", "priority": "HIGH"}
    ])
    jira.get_sprint_board = AsyncMock(return_value={"name": "Sprint 12", "total": 10, "done": 6})
    jira.is_available = MagicMock(return_value=True)

    slack = MagicMock()
    slack.search_messages = AsyncMock(return_value=[])
    slack.is_available = MagicMock(return_value=False)

    github = MagicMock()
    github.list_open_prs = AsyncMock(return_value=[
        {"number": 42, "title": "Fix auth timeout", "author": "alice", "additions": 120}
    ])
    github.is_available = MagicMock(return_value=True)

    registry.get = MagicMock(side_effect=lambda name: {
        "jira": jira, "slack": slack, "github": github
    }.get(name, MagicMock()))

    return registry
