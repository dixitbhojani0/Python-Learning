"""
tests/test_tools.py

Smoke tests for the MCP tool layer — no network, no Redis, no FastMCP.

Covers:
  1. Full tool inventory registers (a missing @mcp.tool() fails loudly here).
  2. is_write_tool classifies every registered tool the way HITL expects —
     in particular jira_add_comment (which once slipped through as read-only).
  3. Boundary validation: garbage pr_id / ticket_id returns a clean error dict
     instead of reaching the connector.
"""
import asyncio

from constants import is_write_tool
from tools import confluence_tools, github_tools, jira_tools, slack_tools


class FakeMCP:
    """Records @mcp.tool() registrations without a real FastMCP server."""
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _register_all() -> dict:
    mcp = FakeMCP()
    for module in (jira_tools, github_tools, slack_tools, confluence_tools):
        module.register(mcp, registry=None)
        module.register_writes(mcp, registry=None)
    return mcp.tools


EXPECTED_READS = {
    "jira_search_tickets", "jira_get_ticket", "jira_get_sprint_board",
    "jira_get_blocked_tickets", "jira_get_project_members", "jira_find_similar_tickets",
    "github_list_open_prs", "github_search_prs", "github_get_pr_details", "github_is_collaborator",
    "slack_search_messages", "slack_get_channel_history",
    "confluence_get_pages", "confluence_get_page_content", "confluence_get_all_page_texts",
    "confluence_get_page_attachments", "confluence_download_attachment",
}

EXPECTED_WRITES = {
    "jira_create_ticket", "jira_assign_ticket", "jira_update_ticket", "jira_add_comment",
    "github_assign_reviewer", "github_approve_pr", "github_request_changes_pr",
    "slack_send_message",
}


def test_full_tool_inventory_registers():
    assert set(_register_all()) == EXPECTED_READS | EXPECTED_WRITES


def test_write_classification_matches_hitl_expectations():
    for name in EXPECTED_WRITES:
        assert is_write_tool(name), f"{name} must be classified WRITE (HITL-gated)"
    for name in EXPECTED_READS:
        assert not is_write_tool(name), f"{name} must be classified READ (autonomous-safe)"


def test_invalid_pr_id_rejected_at_boundary():
    tools = _register_all()
    for tool_name in ("github_get_pr_details", "github_assign_reviewer",
                      "github_approve_pr", "github_request_changes_pr"):
        args = {"pr_id": "not-a-pr"}
        if tool_name == "github_assign_reviewer":
            args["reviewer"] = "alice"
        # registry=None: reaching the connector would raise AttributeError,
        # so a clean error dict proves validation fired first.
        result = asyncio.run(tools[tool_name](**args))
        assert result.get("error"), f"{tool_name} accepted garbage pr_id"
        assert result.get("success") is not True


def test_valid_pr_id_formats_pass_validation():
    for good in ("PR-49", "pr-49", "49", "#49"):
        assert github_tools._invalid_pr_id(good) is None
    for bad in ("", "PR-", "abc", "PR-49x", "49; rm -rf"):
        assert github_tools._invalid_pr_id(bad) is not None


def test_invalid_ticket_id_rejected_at_boundary():
    tools = _register_all()
    for tool_name, args in (
        ("jira_get_ticket",    {"ticket_id": "not a key"}),
        ("jira_assign_ticket", {"ticket_id": "12345", "account_id": "abc"}),
        ("jira_update_ticket", {"ticket_id": "drop table", "summary": "x"}),
        ("jira_add_comment",   {"ticket_id": "", "comment": "hi"}),
    ):
        result = asyncio.run(tools[tool_name](**args))
        assert result.get("error"), f"{tool_name} accepted garbage ticket_id"


def test_valid_ticket_keys_pass_validation():
    for good in ("SDLC-5", "sdlc-5", "AB2-123"):
        assert jira_tools._invalid_ticket_id(good) is None
