"""
tests/unit/test_tool_use_gather_chaining.py

B9 audit (2026-10-06): B9 claims the agent "never lets the LLM see a tool
result and decide the next tool" — true for the 5 fixed-tool specialist
agents (ticket/risk/pr_review/release_readiness; call_mcp_tool directly, by
design — see B7 Step 4's "Design distinction" note), but NOT true for
gather_via_tools() (MCPAgent + NotifyAgent's dynamic-fallback path), which
already runs a real multi-round bind_tools loop. Existing tests in
test_tool_use_gather.py only ever exercise a single tool-call round; this
proves genuine round-2 chaining: the model sees round 1's tool RESULT and
picks a *different* tool for round 2 based on it, which only a loop that
feeds results back into the next model call can produce.

No Docker, no real MCP server, no real LLM — get_mcp_tools/ainvoke_tool and
the chat model are mocked.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from backend.mcp_client import tool_use


def _fake_tool(name: str):
    return SimpleNamespace(name=name)


@pytest.mark.asyncio
async def test_second_round_tool_choice_depends_on_first_rounds_result():
    """Round 1 returns a ticket mentioning PR-7; round 2 must see that result
    (via the ToolMessage appended to `messages`) before choosing to look up
    PR-7 specifically — proving the loop round-trips results back to the model
    rather than firing a fixed, pre-decided sequence of calls."""
    round1_call = AIMessage(content="", tool_calls=[
        {"name": "jira_get_ticket", "args": {"ticket_id": "SDLC-5"}, "id": "1"},
    ])
    round2_call = AIMessage(content="", tool_calls=[
        {"name": "github_search_prs", "args": {"query": "PR-7"}, "id": "2"},
    ])
    done_message = AIMessage(content="DONE")
    turns = [round1_call, round2_call, done_message]

    # `messages` is one mutable list the loop appends to every round, so a mock's
    # auto-recorded call_args (a reference, not a copy) would show the FINAL list
    # state for every past call once the test asserts — snapshot it ourselves at
    # call-time instead, which is the only way to see what round 2 actually saw.
    seen_messages_per_round: list[list] = []

    async def _ainvoke(messages):
        seen_messages_per_round.append(list(messages))
        return turns[len(seen_messages_per_round) - 1]

    fake_chat_model = MagicMock()
    fake_chat_model.bind_tools.return_value = fake_chat_model
    fake_chat_model.ainvoke = AsyncMock(side_effect=_ainvoke)
    fake_provider = MagicMock()
    fake_provider.get_chat_model.return_value = fake_chat_model

    async def _invoke_side_effect(tool, args):
        if tool.name == "jira_get_ticket":
            return {"ticket_id": "SDLC-5", "status": "IN_PROGRESS", "linked_pr": "PR-7"}
        return {"pr": "PR-7", "status": "OPEN"}

    with patch.object(tool_use, "get_mcp_tools", AsyncMock(return_value=[
            _fake_tool("jira_get_ticket"), _fake_tool("github_search_prs")])), \
         patch.object(tool_use, "ainvoke_tool", AsyncMock(side_effect=_invoke_side_effect)), \
         patch.object(tool_use.LLMFactory, "get_provider", return_value=fake_provider):
        result = await tool_use.gather_via_tools("what's the status of SDLC-5 and its PR?")

    # Both rounds executed, in order, with 3 model turns total (round1, round2, DONE).
    assert result.tools_called == ["jira_get_ticket", "github_search_prs"]
    assert fake_chat_model.ainvoke.await_count == 3

    # The decisive proof: round 2's model call received round 1's actual tool
    # RESULT as a message (not just the original query) — i.e. the loop fed the
    # result back in, which is what let the model choose "PR-7" specifically.
    round2_messages = seen_messages_per_round[1]
    tool_messages = [m for m in round2_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "PR-7" in tool_messages[0].content


def demo() -> None:
    """ponytail self-check: run via `python -m tests.unit.test_tool_use_gather_chaining`."""
    import asyncio
    asyncio.run(test_second_round_tool_choice_depends_on_first_rounds_result())
    print("OK — gather_via_tools chains a second tool call off the first's real result")


if __name__ == "__main__":
    demo()
