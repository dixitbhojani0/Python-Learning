"""
Embedding registry tests — same self-registering pattern already proven for
LLM and tool registries (test_llm_registry.py, test_tool_registry.py). This
file was the one genuine gap among the three registries: mock/Gemini
embedding *providers* each had their own tests, but the registry mechanism
itself (registration, duplicate protection, reset) never got a dedicated one.
"""
from __future__ import annotations

import pytest

from backend.app.adapters.embedding import providers as _providers  # noqa: F401  (triggers registration)
from backend.app.adapters.embedding.base import BaseEmbeddingProvider
from backend.app.adapters.embedding.registry import (
    EmbeddingRegistry,
    ProviderAlreadyRegisteredError,
    UnknownProviderError,
)


class _DummyEmbeddingProvider(BaseEmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def get_dimensions(self) -> int:
        return 1


class _AltDummyEmbeddingProvider(BaseEmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    def get_dimensions(self) -> int:
        return 1


@pytest.fixture
def isolated_registry():
    snapshot = dict(EmbeddingRegistry._registry)
    yield EmbeddingRegistry
    EmbeddingRegistry._registry = snapshot


# ── Positive ──────────────────────────────────────────────────────────────

def test_mock_and_gemini_providers_are_registered_on_import():
    assert {"mock", "gemini"}.issubset(set(EmbeddingRegistry.available()))


async def test_create_and_use_the_mock_provider():
    provider = EmbeddingRegistry.create("mock")
    [vector] = await provider.embed(["hello"])
    assert len(vector) == provider.get_dimensions()


# ── Negative ──────────────────────────────────────────────────────────────

def test_unknown_provider_raises_with_available_list_in_message():
    with pytest.raises(UnknownProviderError) as exc_info:
        EmbeddingRegistry.create("does-not-exist")
    assert "mock" in str(exc_info.value)


def test_registering_same_name_with_different_class_raises(isolated_registry):
    isolated_registry.register("dup", _DummyEmbeddingProvider)
    with pytest.raises(ProviderAlreadyRegisteredError):
        isolated_registry.register("dup", _AltDummyEmbeddingProvider)


# ── Edge ──────────────────────────────────────────────────────────────────

def test_registering_same_name_with_same_class_again_is_a_no_op(isolated_registry):
    isolated_registry.register("dup2", _DummyEmbeddingProvider)
    isolated_registry.register("dup2", _DummyEmbeddingProvider)
    assert isolated_registry.available().count("dup2") == 1


def test_reset_clears_the_registry(isolated_registry):
    isolated_registry.reset()
    assert isolated_registry.available() == []
    with pytest.raises(UnknownProviderError):
        isolated_registry.create("mock")


# ── Side effects ────────────────────────────────────────────────────────

def test_isolated_registry_fixture_restores_state_after_test(isolated_registry):
    isolated_registry.reset()
    isolated_registry.register("temp", _DummyEmbeddingProvider)
    assert isolated_registry.available() == ["temp"]


def test_registry_state_survived_the_previous_tests_reset_cleanly():
    assert {"mock", "gemini"}.issubset(set(EmbeddingRegistry.available()))
    assert "temp" not in EmbeddingRegistry.available()
    assert "dup" not in EmbeddingRegistry.available()
