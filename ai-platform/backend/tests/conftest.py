"""
Shared pytest fixtures. Importing backend.app.main (directly or transitively)
triggers LLM provider registration exactly once per test process — individual
tests that need a clean registry use the `isolated_registry` fixture below
instead of relying on process-wide state.
"""
from __future__ import annotations

import pytest

from backend.app.adapters.llm.registry import LLMRegistry


@pytest.fixture
def isolated_registry():
    """
    Snapshot the registry, yield control, then restore it — so a test that
    calls LLMRegistry.reset() (or registers a throwaway provider) can never
    leak state into a test that runs after it, regardless of execution order.
    """
    snapshot = dict(LLMRegistry._registry)
    yield LLMRegistry
    LLMRegistry._registry = snapshot
