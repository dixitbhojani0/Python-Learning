"""
backend/orchestrator/actions.py

Approved-action execution shared by the HITL REST route and the Slack webhook.

Both entry points (POST /api/hitl/approve and the Slack Approve button) execute
the same Jira write over MCP — this module is that single path, so neither
route imports the other.
"""
import json
import logging

from backend.core.settings import settings
from backend.mcp_client.client import call_mcp_tool

logger = logging.getLogger(__name__)


async def mcp_write(tool: str, args: dict) -> dict:
    """
    Execute an approved WRITE action over the MCP server and return a dict result.

    This is the one execution path for HITL writes (create/assign/approve/notify),
    so the write side is real MCP just like reads. MCP may return the result as a
    JSON string (or single-item list); normalize to a dict so callers can read
    fields like .get("id") / .get("status"). Returns {} on unparseable/empty output.
    """
    raw = await call_mcp_tool(tool, args)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except (ValueError, TypeError):
            return {"result": raw}
    return {}


async def _dm_assignee(jira_account_id: str, ticket_title: str, ticket_id: str) -> None:
    """
    DM the assigned person in Slack after a ticket is created.
    Reads config/slack_users.yaml to resolve jira_account_id → slack_user_id,
    then sends the DM via the MCP slack_send_message tool (consistent with all
    other Slack calls — never direct httpx here).
    Best-effort: any failure is logged but never raised.
    """
    if not jira_account_id:
        return
    try:
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parents[2] / "config" / "slack_users.yaml"
        data   = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        member = next(
            (m for m in data.get("team", []) if m.get("jira_account_id") == jira_account_id),
            None,
        )
        if not member:
            logger.info("_dm_assignee: no Slack mapping for jira_account_id=%s", jira_account_id)
            return
        slack_user_id = member.get("slack_user_id", "")
        if not slack_user_id or slack_user_id.startswith("replace_with"):
            return
        # Passing a Slack user ID (e.g. "U01ABC") as the channel opens a DM
        # automatically — Slack's chat.postMessage API supports this natively.
        await mcp_write("slack_send_message", {
            "channel": slack_user_id,
            "message": f"📋 You've been assigned *{ticket_id}*: {ticket_title}",
        })
        logger.info("_dm_assignee: DM sent to slack_user=%s for ticket=%s", slack_user_id, ticket_id)
    except Exception:
        logger.exception("_dm_assignee: failed — ticket %s still created", ticket_id)


async def execute_create_ticket(proposal: dict, approver_role: str = "", approver_name: str = "") -> str:
    """
    Create the Jira ticket over MCP (`jira_create_ticket`).

    One canonical create path (B2 fix): the MCP server's Jira connector uses
    real Jira when JIRA_TOKEN is set, else its mock — so we never fabricate a fake
    ticket number here. On failure we say so honestly instead of inventing an id.

    E2: when a STAKEHOLDER creates the ticket, notify the developer team on Slack
    so a dev picks it up and fills in real effort/comments (the role matrix says
    stakeholder create → notify developer).
    """
    title               = proposal.get("title", "Untitled")
    description         = proposal.get("description", "")
    priority            = proposal.get("priority", "MEDIUM")
    issue_type          = proposal.get("issue_type", "Story")
    assignee            = proposal.get("assignee", "unassigned")
    assignee_account_id = proposal.get("assignee_account_id", "")
    project             = proposal.get("project", settings.DEFAULT_PROJECT)
    labels              = proposal.get("labels", [])

    # sprint_id="" lets jira_create_ticket resolve the active sprint internally
    sprint_id = ""

    try:
        result = await mcp_write("jira_create_ticket", {
            "title": title, "description": description, "priority": priority,
            "issue_type": issue_type, "labels": ",".join(labels) if labels else "",
            "assignee_account_id": assignee_account_id, "sprint_id": sprint_id,
        })
    except Exception:
        logger.exception("hitl/approve: MCP jira_create_ticket failed")
        result = {}
    ticket_id  = result.get("id", "")
    ticket_url = result.get("url", "")
    if ticket_id:
        sprint_note = f"Sprint {sprint_id}" if sprint_id else "backlog"
        logger.info("hitl/approve: ticket created via MCP — %s (sprint=%s)", ticket_id, sprint_id)
        await _dm_assignee(assignee_account_id, title, ticket_id)
        url_line = f"[Open in Jira]({ticket_url})\n\n" if ticket_url else ""

        # E2: stakeholder-created ticket → notify the dev team so they triage it.
        notify_note = ""
        if approver_role == "stakeholder":
            try:
                await mcp_write("slack_send_message", {
                    "channel": "#backend",
                    "message": (
                        f"📋 New ticket *{ticket_id}* raised by {approver_name} (stakeholder): "
                        f"{title}. Please review, assign an owner, and add effort/comments."
                    ),
                })
                notify_note = "\n\n_The developer team has been notified on Slack to pick this up._"
                logger.info("E2: stakeholder-create Slack notify sent for %s", ticket_id)
            except Exception:
                logger.exception("E2: stakeholder-create Slack notify failed — ticket still created")

        return (
            f"✅ **Ticket {ticket_id} created in Jira.**\n\n"
            f"**Title**: {title}\n"
            f"**Priority**: {priority}\n"
            f"**Assignee**: {assignee}\n"
            f"**Project**: {project}\n"
            f"**Sprint**: {sprint_note}\n\n"
            f"{url_line}"
            f"The ticket has been added to {sprint_note}."
            f"{notify_note}"
        )

    error_detail = result.get("error", "")
    logger.warning("hitl/approve: jira_create_ticket via MCP returned no id (%s)", error_detail)
    reason = f"\n\n_Reason: {error_detail}_" if error_detail else ""
    return (
        "⚠️ **Could not create the ticket** — the Jira MCP tool returned no id."
        f"{reason}\n\n"
        "Please retry, or create it manually in Jira."
    )
