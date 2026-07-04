"""
tools/jira_tools.py

Jira READ and WRITE tools exposed over MCP.
Each tool delegates to JiraConnector via the shared MCPRegistry.
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

        Args:
            project: Jira project key; empty = default project.

        Returns: dict with sprint, total_tickets, done, in_progress, blocked,
        not_started, completion_pct, risk_level.
        """
        logger.info("tool jira_get_sprint_board(project=%r)", project)
        return await registry.get("jira").get_sprint_board(project)

    @mcp.tool()
    async def jira_get_blocked_tickets(project: str = "") -> list[dict]:
        """Return tickets currently blocked for the project.

        Args:
            project: Jira project key; empty = default project.

        Returns: list of blocked tickets.
        """
        logger.info("tool jira_get_blocked_tickets(project=%r)", project)
        return await registry.get("jira").get_blocked_tickets(project)

    @mcp.tool()
    async def jira_get_project_members(project: str = "") -> list[dict]:
        """List users who can be assigned to tickets in the project.

        Args:
            project: Jira project key; empty = default project.

        Returns: list of members with name, display_name, account_id, email, active.
        """
        logger.info("tool jira_get_project_members(project=%r)", project)
        return await registry.get("jira").get_project_members(project)

    @mcp.tool()
    async def jira_find_similar_tickets(title: str, project: str = "") -> list[dict]:
        """Find Jira tickets whose summary or description overlaps with `title`.

        Call BEFORE jira_create_ticket to surface possible duplicates.

        Args:
            title: candidate ticket title to compare.
            project: Jira project key; empty = default project.

        Returns: list of matching tickets.
        """
        logger.info("tool jira_find_similar_tickets(title=%r, project=%r)", title, project)
        return await registry.get("jira").search_tickets(title, project)

    logger.info("jira_tools: registered 6 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """Add Jira WRITE tools. State-changing — run only via HITL approval."""

    @mcp.tool()
    async def jira_create_ticket(
        title: str,
        description: str,
        priority: str = "MEDIUM",
        issue_type: str = "Story",
        labels: str = "",
        assignee_account_id: str = "",
        sprint_id: str = "",
        project: str = "",
    ) -> dict:
        """Create a new Jira ticket. WRITE — requires human approval (HITL) before use.

        Args:
            title: the ticket summary/title.
            description: the ticket body.
            priority: one of LOW, MEDIUM, HIGH, CRITICAL (default MEDIUM).
            issue_type: Story | Task | Bug (default Story).
            labels: comma-separated labels; empty for none.
            assignee_account_id: Jira Cloud accountId; empty = unassigned.
            sprint_id: numeric id, "current"/"active"/"", or "backlog".
            project: Jira project key; empty = default.

        Returns: {"id": "<KEY>", "url": ..., "sprint_resolved": bool, "note": "..."}.
        """
        label_list = [s.strip() for s in labels.split(",") if s.strip()]
        sp = sprint_id.strip()
        sid: int | None = None
        sprint_resolved = False
        note = ""
        if sp.isdigit():
            sid = int(sp)
            sprint_resolved = True
        elif sp.lower() in {"backlog", "none", "no"}:
            pass
        else:
            sid = await registry.get("jira").get_active_sprint_id(project)
            if sid:
                sprint_resolved = True
                logger.info("jira_create_ticket: sprint_id=%r → resolved to active sprint %d", sp, sid)
            else:
                note = "Active sprint id not found — ticket landed in backlog."

        logger.info("tool jira_create_ticket(title=%r, priority=%r, sprint=%s)", title, priority, sid)
        result = await registry.get("jira").create_ticket(
            title=title, description=description, priority=priority,
            issue_type=issue_type, labels=label_list,
            assignee_account_id=assignee_account_id, sprint_id=sid,
        )
        if sid and result.get("id"):
            added = await registry.get("jira").add_issue_to_sprint(result["id"], sid)
            sprint_resolved = sprint_resolved and added
            if not added:
                note = (note + " ").lstrip() + "Sprint API rejected the post-create assignment — ticket may still be in backlog."
        result["sprint_resolved"] = sprint_resolved
        if note:
            result["note"] = note
        return result

    @mcp.tool()
    async def jira_assign_ticket(ticket_id: str, account_id: str) -> dict:
        """Assign a Jira ticket to a user. WRITE — requires HITL approval.

        Args:
            ticket_id: issue key, e.g. "SDLC-5".
            account_id: the assignee's Jira Cloud accountId.
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
        """
        logger.info("tool jira_update_ticket(ticket_id=%r)", ticket_id)
        return await registry.get("jira").update_ticket(ticket_id, description=description, summary=summary)

    @mcp.tool()
    async def jira_add_comment(ticket_id: str, comment: str) -> dict:
        """Add a comment to a Jira ticket. WRITE — requires HITL approval.

        Args:
            ticket_id: issue key, e.g. "SDLC-5".
            comment: the comment text.
        """
        logger.info("tool jira_add_comment(ticket_id=%r)", ticket_id)
        return await registry.get("jira").add_comment(ticket_id, comment)

    logger.info("jira_tools: registered 4 write tools")
