"""
backend/mcp_server/tools/jira_tools.py

Jira READ tools, exposed over MCP. Each tool delegates to the existing
JiraConnector (real Jira REST, or mock when creds aren't set) via the shared
registry — no Jira logic is duplicated here.

Tool docstrings are the descriptions the LLM reads to decide *when* to call
each tool, so they're written as specs (what it does, what to pass, what comes
back). Write tools (create/update/assign) are added in Step 4, behind HITL.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add Jira read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def jira_search_tickets(query: str, project: str = "") -> list[dict]:
        """Search Jira issues by natural language and return matching tickets.

        Use for "find the ticket about X", "tickets assigned to Alice",
        "in-progress tickets", or a specific key like "SDLC-5".

        Args:
            query: natural-language search, a ticket key (e.g. "SDLC-5"), an
                   assignee ("assigned to alice"), or a status ("blocked").
            project: Jira project key (e.g. "SDLC"); empty = default project.

        Returns: list of tickets, each with id, title, status, priority,
        assignee, description, labels, blockers, created, updated.
        """
        logger.info("tool jira_search_tickets(query=%r, project=%r)", query, project)
        return await registry.get("jira").search_tickets(query, project)

    @mcp.tool()
    async def jira_get_ticket(ticket_id: str) -> dict:
        """Fetch one Jira ticket by its exact key (e.g. "SDLC-5"), incl. comments.

        Use when the user names a specific ticket. Faster/exacter than search.

        Args:
            ticket_id: the issue key, e.g. "SDLC-5".

        Returns: the ticket dict, or {"error": ..., "ticket_id": ...} if not found.
        """
        logger.info("tool jira_get_ticket(ticket_id=%r)", ticket_id)
        ticket = await registry.get("jira").get_ticket(ticket_id)
        return ticket if ticket else {"error": "ticket not found", "ticket_id": ticket_id}

    @mcp.tool()
    async def jira_get_sprint_board(project: str = "") -> dict:
        """Return current sprint summary stats for the project.

        Use for "sprint status / progress / completion / how many done".

        Args:
            project: Jira project key; empty = default project.

        Returns: dict with sprint, total_tickets, done, in_progress, blocked,
        not_started, completion_pct, risk_level.
        """
        logger.info("tool jira_get_sprint_board(project=%r)", project)
        return await registry.get("jira").get_sprint_board(project)

    @mcp.tool()
    async def jira_get_blocked_tickets(project: str = "") -> list[dict]:
        """Return tickets currently blocked (status Blocked, label blocked, or
        priority Blocker) for the project.

        Use for "what's blocking us", "delivery risk", blocker analysis.

        Args:
            project: Jira project key; empty = default project.

        Returns: list of blocked tickets (same fields as jira_search_tickets).
        """
        logger.info("tool jira_get_blocked_tickets(project=%r)", project)
        return await registry.get("jira").get_blocked_tickets(project)

    @mcp.tool()
    async def jira_get_project_members(project: str = "") -> list[dict]:
        """List users who can be assigned to tickets in the project.

        Use to resolve an assignee name to an accountId before assigning/creating.

        Args:
            project: Jira project key; empty = default project.

        Returns: list of members, each with name, display_name, account_id, email, active.
        """
        logger.info("tool jira_get_project_members(project=%r)", project)
        return await registry.get("jira").get_project_members(project)

    logger.info("jira_tools: registered 5 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """
    Add Jira WRITE tools. These change state, so they are excluded from the
    autonomous gather loop (client.is_write_tool) and run only via the approved
    HITL execution path. Tool names carry a write verb by convention.
    """

    @mcp.tool()
    async def jira_create_ticket(
        title: str,
        description: str,
        priority: str = "MEDIUM",
        issue_type: str = "Story",
        labels: str = "",
        assignee_account_id: str = "",
        sprint_id: str = "",
    ) -> dict:
        """Create a new Jira ticket. WRITE — requires human approval (HITL) before use.

        Args:
            title: the ticket summary/title.
            description: the ticket body.
            priority: one of LOW, MEDIUM, HIGH, CRITICAL (default MEDIUM).
            issue_type: Story | Task | Bug (default Story).
            labels: comma-separated labels (e.g. "backend,bug"); empty for none.
            assignee_account_id: Jira Cloud accountId to assign; empty = unassigned.
            sprint_id: numeric sprint id to add the ticket to; empty = backlog.

        Returns: {"id": "<KEY>", "url": ...} on success, or {} on failure.
        """
        label_list = [s.strip() for s in labels.split(",") if s.strip()]
        sid = int(sprint_id) if sprint_id.strip().isdigit() else None
        logger.info("tool jira_create_ticket(title=%r, priority=%r, sprint=%s)", title, priority, sid)
        return await registry.get("jira").create_ticket(
            title=title, description=description, priority=priority,
            issue_type=issue_type, labels=label_list,
            assignee_account_id=assignee_account_id, sprint_id=sid,
        )

    @mcp.tool()
    async def jira_assign_ticket(ticket_id: str, account_id: str) -> dict:
        """Assign a Jira ticket to a user. WRITE — requires HITL approval.

        Args:
            ticket_id: issue key, e.g. "SDLC-5".
            account_id: the assignee's Jira Cloud accountId.

        Returns: {"success": bool, ...}.
        """
        logger.info("tool jira_assign_ticket(ticket_id=%r, account_id=%r)", ticket_id, account_id)
        return await registry.get("jira").assign_ticket(ticket_id, account_id)

    @mcp.tool()
    async def jira_update_ticket(ticket_id: str, summary: str = "", description: str = "") -> dict:
        """Update a Jira ticket's summary and/or description. WRITE — requires HITL approval.

        Args:
            ticket_id: issue key, e.g. "SDLC-5".
            summary: new title (empty = unchanged).
            description: new body (empty = unchanged).

        Returns: {"success": bool, ...}.
        """
        logger.info("tool jira_update_ticket(ticket_id=%r)", ticket_id)
        return await registry.get("jira").update_ticket(
            ticket_id, description=description, summary=summary,
        )

    @mcp.tool()
    async def jira_add_comment(ticket_id: str, comment: str) -> dict:
        """Add a comment to a Jira ticket. WRITE — requires HITL approval.

        Use when a developer logs effort/details (e.g. "this is a 3-day refactor")
        so status answers reflect the real story.

        Args:
            ticket_id: issue key, e.g. "SDLC-5".
            comment: the comment text.

        Returns: {"success": bool, "ticket_id": ...}.
        """
        logger.info("tool jira_add_comment(ticket_id=%r)", ticket_id)
        return await registry.get("jira").add_comment(ticket_id, comment)

    logger.info("jira_tools: registered 4 write tools")
