"""
Tool registry tests — same self-registering pattern already proven for
LLM/embedding providers (test_llm_registry.py). Uses the real builtin
tools (imported once via conftest-independent import below) so this also
double-checks that importing backend.app.tools.builtin actually registers
all three without raising.
"""
from __future__ import annotations

import uuid

import pytest

from backend.app.tools import builtin as _builtin  # noqa: F401  (triggers registration)
from backend.app.tools.base import BaseTool
from backend.app.tools.registry import ProviderAlreadyRegisteredError, ToolRegistry, UnknownProviderError


class _DummyTool(BaseTool):
    name = "dummy"
    description = "test double"
    risk_level = "low"

    async def execute(self, *, session, tenant_id, user_id, **kwargs):
        return {"ok": True}


class _AltDummyTool(BaseTool):
    name = "dummy"
    description = "a different implementation"
    risk_level = "low"

    async def execute(self, *, session, tenant_id, user_id, **kwargs):
        return {"ok": False}


@pytest.fixture
def isolated_registry():
    snapshot = dict(ToolRegistry._registry)
    yield ToolRegistry
    ToolRegistry._registry = snapshot


# ── Positive ──────────────────────────────────────────────────────────────

def test_builtin_tools_are_registered_on_import():
    assert {"calculator", "current_time", "delete_all_documents"}.issubset(set(ToolRegistry.available()))


async def test_create_and_use_a_low_risk_tool():
    tool = ToolRegistry.create("current_time")
    result = await tool.execute(session=None, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())
    assert "utc_time" in result


# ── Negative ──────────────────────────────────────────────────────────────

def test_unknown_tool_raises_with_available_list_in_message():
    with pytest.raises(UnknownProviderError) as exc_info:
        ToolRegistry.create("does-not-exist")
    assert "calculator" in str(exc_info.value)


def test_registering_same_name_with_different_class_raises(isolated_registry):
    isolated_registry.register("dup", _DummyTool)
    with pytest.raises(ProviderAlreadyRegisteredError):
        isolated_registry.register("dup", _AltDummyTool)


# ── Edge ──────────────────────────────────────────────────────────────────

def test_registering_same_name_with_same_class_again_is_a_no_op(isolated_registry):
    isolated_registry.register("dup2", _DummyTool)
    isolated_registry.register("dup2", _DummyTool)
    assert isolated_registry.available().count("dup2") == 1


def test_reset_clears_the_registry(isolated_registry):
    isolated_registry.reset()
    assert isolated_registry.available() == []
    with pytest.raises(UnknownProviderError):
        isolated_registry.create("calculator")


# ── Side effects ────────────────────────────────────────────────────────

def test_isolated_registry_fixture_restores_state_after_test(isolated_registry):
    isolated_registry.reset()
    isolated_registry.register("temp", _DummyTool)
    assert isolated_registry.available() == ["temp"]


def test_registry_state_survived_the_previous_tests_reset_cleanly():
    assert {"calculator", "current_time", "delete_all_documents"}.issubset(set(ToolRegistry.available()))
    assert "temp" not in ToolRegistry.available()
    assert "dummy" not in ToolRegistry.available()
