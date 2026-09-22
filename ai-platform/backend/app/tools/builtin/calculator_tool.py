"""
backend/app/tools/builtin/calculator_tool.py

Arithmetic via ast.parse + a whitelist walker — never eval()/exec(). A tool
whose input comes from a chat message is, by definition, attacker-reachable
text; eval("__import__('os').system('...')") is exactly the class of bug
this avoids by construction rather than by hoping the regex trigger
upstream (tools/intent.py) is airtight.
"""
from __future__ import annotations

import ast
import operator
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.tools.base import BaseTool, ToolExecutionError
from backend.app.tools.registry import ToolRegistry

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise ToolExecutionError(f"Unsupported expression element: {type(node).__name__}")


def safe_eval_arithmetic(expression: str) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolExecutionError(f"Could not parse expression: {expression!r}") from exc

    try:
        return _eval_node(tree.body)
    except ZeroDivisionError as exc:
        raise ToolExecutionError("Division by zero") from exc


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluates a basic arithmetic expression (+ - * / ** and parentheses)."
    risk_level = "low"

    async def execute(
        self, *, session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, expression: str, **kwargs: object
    ) -> dict:
        return {"result": safe_eval_arithmetic(expression)}


ToolRegistry.register("calculator", CalculatorTool)
