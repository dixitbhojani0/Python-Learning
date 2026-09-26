"""
tests/unit/test_classifier_routing_reason.py

Regression guard for E9 ("why this answer?" trace): llm_classify() already asks
the LLM for a one-line routing `reason` and used to log it, then discard it.
It must now return (agents, reason) so classify_intent can surface it on the
graph state. keyword_classify has no LLM reasoning behind it, so every
fallback path must return reason="" rather than fabricating one.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.orchestrator import classifier


class _FakeStructuredResponse:
    def __init__(self, structured=None, is_empty=False, parse_error=False):
        self.structured  = structured or {}
        self.is_empty    = is_empty
        self.parse_error = parse_error


@pytest.mark.asyncio
async def test_llm_classify_returns_llm_reason_on_success():
    fake_agents_cfg = {
        "ticket_agent": {"enabled": True, "routing_description": "creates/edits tickets"},
    }
    fake_provider = MagicMock()
    fake_provider.generate_structured = AsyncMock(return_value=_FakeStructuredResponse(
        structured={"agents": ["ticket"], "confidence": 0.9, "reason": "query asks to create a ticket"}
    ))

    with patch.object(classifier, "AGENT_INTENT_MAP", {"ticket_agent": "ticket"}), \
         patch.object(classifier, "VALID_INTENTS", {"ticket"}), \
         patch.object(classifier.config, "get_agents", return_value=fake_agents_cfg), \
         patch.object(classifier.config, "get_prompt", return_value="prompt text"), \
         patch.object(classifier.LLMFactory, "get_provider", return_value=fake_provider):
        agents, reason = await classifier.llm_classify("create a ticket for the CORS bug")

    assert agents == ["ticket"]
    assert reason == "query asks to create a ticket"


@pytest.mark.asyncio
async def test_llm_classify_keyword_fallback_has_no_reason():
    """No routing_description in config → falls to keyword_classify before any
    LLM call — must still return a (agents, reason) tuple with reason=""."""
    fake_agents_cfg = {
        "ticket_agent": {"enabled": True, "routing_description": "", "trigger_keywords": ["ticket"]},
    }
    with patch.object(classifier, "AGENT_INTENT_MAP", {"ticket_agent": "ticket"}), \
         patch.object(classifier, "VALID_INTENTS", {"ticket"}), \
         patch.object(classifier.config, "get_agents", return_value=fake_agents_cfg):
        agents, reason = await classifier.llm_classify("please handle my ticket")

    assert agents == ["ticket"]
    assert reason == ""


@pytest.mark.asyncio
async def test_llm_classify_provider_exception_falls_back_with_no_reason():
    fake_agents_cfg = {
        "ticket_agent": {"enabled": True, "routing_description": "creates/edits tickets", "trigger_keywords": ["ticket"]},
    }
    fake_provider = MagicMock()
    fake_provider.generate_structured = AsyncMock(side_effect=RuntimeError("rate limited"))

    with patch.object(classifier, "AGENT_INTENT_MAP", {"ticket_agent": "ticket"}), \
         patch.object(classifier, "VALID_INTENTS", {"ticket"}), \
         patch.object(classifier.config, "get_agents", return_value=fake_agents_cfg), \
         patch.object(classifier.config, "get_prompt", return_value="prompt text"), \
         patch.object(classifier.LLMFactory, "get_provider", return_value=fake_provider):
        agents, reason = await classifier.llm_classify("create a ticket")

    assert agents == ["ticket"]
    assert reason == ""
