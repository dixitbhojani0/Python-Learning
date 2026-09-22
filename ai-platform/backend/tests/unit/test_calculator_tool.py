from __future__ import annotations

import pytest

from backend.app.tools.base import ToolExecutionError
from backend.app.tools.builtin.calculator_tool import safe_eval_arithmetic


# ── Positive ──────────────────────────────────────────────────────────────

def test_basic_addition():
    assert safe_eval_arithmetic("2 + 2") == 4


def test_operator_precedence_and_parentheses():
    assert safe_eval_arithmetic("(2 + 3) * 4") == 20


def test_negative_numbers():
    assert safe_eval_arithmetic("-5 + 10") == 5


def test_exponentiation():
    assert safe_eval_arithmetic("2 ** 8") == 256


# ── Negative — the actual security property ──────────────────────────────

def test_arbitrary_code_execution_attempt_is_rejected_not_executed():
    with pytest.raises(ToolExecutionError):
        safe_eval_arithmetic("__import__('os').system('echo pwned')")


def test_attribute_access_attempt_is_rejected():
    with pytest.raises(ToolExecutionError):
        safe_eval_arithmetic("().__class__.__bases__[0]")


def test_function_call_attempt_is_rejected():
    with pytest.raises(ToolExecutionError):
        safe_eval_arithmetic("print(1)")


def test_garbage_input_raises_a_clean_error_not_a_raw_syntax_error():
    # "this is not math" is actually VALID Python syntax (a chained `is not`
    # comparison of two identifiers) — it hits the "unsupported node type"
    # branch (Compare isn't in the allowed op sets), not a SyntaxError. Kept
    # as its own case: it's the more common shape of "nonsense a user might
    # type," distinct from genuinely unparseable input below.
    with pytest.raises(ToolExecutionError):
        safe_eval_arithmetic("this is not math")


def test_genuinely_unparseable_input_raises_a_clean_error():
    # An incomplete expression — a real ast.parse() SyntaxError, the other
    # branch safe_eval_arithmetic catches and re-raises as ToolExecutionError.
    with pytest.raises(ToolExecutionError):
        safe_eval_arithmetic("2 +")


# ── Edge ──────────────────────────────────────────────────────────────────

def test_division_by_zero_raises_a_clean_tool_error():
    with pytest.raises(ToolExecutionError):
        safe_eval_arithmetic("1 / 0")


# ── Side effects ────────────────────────────────────────────────────────

def test_evaluation_is_pure_and_deterministic():
    assert safe_eval_arithmetic("3 + 4 * 2") == safe_eval_arithmetic("3 + 4 * 2")
