"""
backend/mcp_client/tool_use.py

The MCP tool-gathering loop — the agentic core of the B7 restructure.

Given a user query, an LLM (via the provider seam) reads the MCP tool catalogue,
decides which tools to call (and chains/repeats as needed), and we execute those
calls over MCP. We return the COLLECTED LIVE DATA + a call trace — NOT a final
answer. The answer is written downstream by the app's own generation + persona +
faithfulness pipeline ("gather-then-synthesize"), so tool selection and answer
authoring stay decoupled.

Why a manual bind_tools loop (not create_react_agent)?
  - we want the tool *results*, not the agent's prose answer (no wasted generation)
  - we control the iteration cap (no runaway loop / quota burn)
  - we capture the trace (tools + args + results) for explainability (E9)

Provider-agnostic: the chat model comes from LLMFactory.get_provider().get_chat_model(),
so swapping Groq → Gemini → OpenAI changes nothing here.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.core.config_loader import config
from backend.mcp_client.client import get_mcp_tools, normalize_tool_result
from backend.providers.factory import LLMFactory

logger = logging.getLogger(__name__)


def _to_text(value: Any) -> str:
    """Render a (normalized) tool result as text for the model / prompt."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return str(value)

# Gather-phase instruction: call tools to collect data, then stop. We discard any
# prose the model writes — the app synthesizes the answer — so we tell it to keep
# the closing turn short.
_GATHER_SYSTEM = (
    "You are the data-gathering step of an SDLC assistant. You have live tools "
    "(Jira, GitHub, Slack, Confluence, Teams, Drive). Decide which tool(s) are "
    "needed to answer the user's request and call them — chain multiple tools when "
    "the request spans sources, but call each tool at most once per distinct need "
    "and never repeat an identical call. When you have gathered enough data, reply "
    "with just the word DONE. Do not write the final answer; another step does that."
)

# Hard cap on model<->tool round-trips, so a confused model can't spin or burn quota.
# Read from llm.yaml > routing.tool_gathering.max_iterations; fallback = 5.
_tg_cfg = lambda: config.get_llm_config().get("routing", {}).get("tool_gathering", {})  # noqa: E731
_MAX_ITERS    = int(_tg_cfg().get("max_iterations", 5))

# B7e: when the model emits several tool calls in one turn, run them concurrently
# (independent reads) under this cap, with per-call failure isolation.
# Read from llm.yaml > routing.tool_gathering.max_parallel; fallback = 4.
_MAX_PARALLEL = int(_tg_cfg().get("max_parallel", 4))


@dataclass
class ToolCall:
    """One executed MCP tool call and its result (for context + trace)."""
    tool: str
    args: dict
    result: Any
    error: str = ""


@dataclass
class ToolGatherResult:
    """Output of the gather loop — live data for synthesis, plus a trace."""
    tools_called: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.calls

    def as_context(self) -> str:
        """Format the gathered live data as a prompt block for the synthesis step."""
        if not self.calls:
            return ""
        blocks = ["## Live data gathered via MCP tools"]
        for c in self.calls:
            head = f"### {c.tool}({', '.join(f'{k}={v!r}' for k, v in c.args.items())})"
            blocks.append(head)
            blocks.append(f"ERROR: {c.error}" if c.error else _to_text(c.result))
        return "\n".join(blocks)


async def gather_via_tools(
    query: str,
    system: str = _GATHER_SYSTEM,
    max_iters: int = _MAX_ITERS,
) -> ToolGatherResult:
    """
    Run the LLM tool-use loop to gather live data for `query`.

    Returns a ToolGatherResult with the executed calls + their results. Does NOT
    produce a user-facing answer. Safe-degrading: if the MCP server is unreachable
    or no tools are found, returns an empty result (caller falls back to RAG-only).
    """
    try:
        tools = await get_mcp_tools()
    except BaseException:
        logger.exception("gather_via_tools: MCP tools unavailable — returning empty (RAG-only fallback)")
        return ToolGatherResult()
    if not tools:
        return ToolGatherResult()

    model = LLMFactory.get_provider().get_chat_model().bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}
    sem = asyncio.Semaphore(_MAX_PARALLEL)

    messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=query)]
    out = ToolGatherResult()

    async def _execute(tc: dict) -> tuple[ToolCall, ToolMessage]:
        """Run one tool call (B7f-normalized), isolated so one failure can't abort the turn."""
        name, args, tc_id = tc["name"], tc.get("args", {}), tc.get("id", "")
        tool = tools_by_name.get(name)
        if tool is None:
            logger.warning("gather_via_tools: model asked for unknown tool %r", name)
            return (ToolCall(name, args, None, error="unknown tool"),
                    ToolMessage(content=f"unknown tool {name}", tool_call_id=tc_id))
        async with sem:
            try:
                result = normalize_tool_result(await tool.ainvoke(args))
                logger.info("gather_via_tools: %s(%s) ok", name, args)
                return (ToolCall(name, args, result),
                        ToolMessage(content=_to_text(result), tool_call_id=tc_id))
            except Exception as err:
                logger.exception("gather_via_tools: tool %s failed", name)
                return (ToolCall(name, args, None, error=str(err)),
                        ToolMessage(content=f"error: {err}", tool_call_id=tc_id))

    for _ in range(max_iters):
        try:
            ai: AIMessage = await model.ainvoke(messages)
        except Exception:
            # The provider can reject its OWN malformed tool call (e.g. Groq 400
            # tool_use_failed when it emits invalid JSON args). That must not crash
            # the request — degrade to whatever we've gathered so far (or empty, →
            # RAG-only), consistent with how per-tool failures are isolated below.
            logger.exception("gather_via_tools: LLM turn failed — stopping with %d call(s) gathered", len(out.calls))
            break
        messages.append(ai)
        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            break  # model is done gathering

        # B7e: execute this turn's tool calls concurrently; preserve order so each
        # ToolMessage still follows the AIMessage that requested it.
        executed = await asyncio.gather(*(_execute(tc) for tc in tool_calls))
        for call, msg in executed:
            out.calls.append(call)
            messages.append(msg)

    out.tools_called = [c.tool for c in out.calls]
    logger.info("gather_via_tools: gathered %d call(s): %s", len(out.calls), out.tools_called)
    return out
