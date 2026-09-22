"""
LLM plugin registry tests — the self-registering pattern from §M. Covers the
same properties the sibling ai-sdlc-assistant project relies on: registration,
lookup, duplicate-name protection, and clean reset for test isolation.
"""
from __future__ import annotations

import pytest

from backend.app.adapters.llm.base import BaseLLMProvider
from backend.app.adapters.llm.registry import (
    LLMRegistry,
    ProviderAlreadyRegisteredError,
    UnknownProviderError,
)
from backend.app.adapters.llm.providers.mock_provider import MockProvider


class _AltProvider(BaseLLMProvider):
    async def generate_text(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        return "alt"

    def get_model_name(self) -> str:
        return "alt-v1"


# ── Positive ──────────────────────────────────────────────────────────────

def test_mock_provider_is_registered_on_import():
    assert "mock" in LLMRegistry.available()


@pytest.mark.asyncio
async def test_create_and_use_mock_provider():
    provider = LLMRegistry.create("mock")
    result = await provider.generate_text("hello")
    assert result == "[mock:mock-echo-v1] hello"
    assert provider.get_model_name() == "mock-echo-v1"


def test_available_lists_all_registered_names(isolated_registry):
    isolated_registry.register("alt", _AltProvider)
    assert "alt" in isolated_registry.available()
    assert "mock" in isolated_registry.available()  # existing registration untouched


# ── Negative ──────────────────────────────────────────────────────────────

def test_unknown_provider_raises_with_available_list_in_message():
    with pytest.raises(UnknownProviderError) as exc_info:
        LLMRegistry.create("does-not-exist")
    assert "mock" in str(exc_info.value)


def test_registering_same_name_with_different_class_raises(isolated_registry):
    isolated_registry.register("dup", MockProvider)
    with pytest.raises(ProviderAlreadyRegisteredError):
        isolated_registry.register("dup", _AltProvider)


# ── Edge ──────────────────────────────────────────────────────────────────

def test_registering_same_name_with_same_class_again_is_a_no_op(isolated_registry):
    isolated_registry.register("dup2", MockProvider)
    isolated_registry.register("dup2", MockProvider)  # must not raise — idempotent re-import
    assert isolated_registry.available().count("dup2") == 1


def test_empty_registry_has_no_available_providers(isolated_registry):
    isolated_registry.reset()
    assert isolated_registry.available() == []
    with pytest.raises(UnknownProviderError):
        isolated_registry.create("mock")


# ── Side effects ────────────────────────────────────────────────────────

def test_isolated_registry_fixture_restores_state_after_test(isolated_registry):
    isolated_registry.reset()
    isolated_registry.register("temp", MockProvider)
    assert isolated_registry.available() == ["temp"]
    # teardown (fixture) restores the snapshot — verified by the next test:


def test_registry_state_survived_previous_tests_reset_cleanly():
    # If the fixture teardown above leaked, "temp" would still be here and/or
    # one of the real providers would be missing. Both would break this
    # assertion. The exact set grows as providers/__init__.py grows — this
    # checks "exactly the real registrations survived", not one hardcoded name.
    assert LLMRegistry.available() == ["gemini", "groq", "mock"]
