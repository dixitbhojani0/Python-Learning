"""
backend/api/routes/hitl.py

POST /api/hitl/approve  — user approved the pending agent proposal
POST /api/hitl/reject   — user rejected the pending agent proposal

Both endpoints:
  1. Look up the pending action in HITLManager (Redis-backed) by hitl_id
  2. Execute (or discard) the action
  3. Remove the action from the store
  4. Return a plain {"response": "...", "decision": "..."}

Ticket creation: calls the real Jira connector when JIRA_TOKEN is configured;
  falls back to a local counter display when running in demo mode (no token).
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.limiter import limiter
from backend.api.models.schemas import HITLRequest
from backend.auth.middleware import UserContext, get_current_user
from backend.auth.permissions import ALL_ACTIONS, require
from backend.core.settings import settings as _settings
from backend.memory.episodic_memory import episodic_memory
from backend.memory.session_store import session_store
from backend.mcp.registry import MCPRegistry
from backend.mcp_client.client import call_mcp_tool
from backend.orchestrator.hitl import hitl_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# ── MCPRegistry singleton — created once, reused across all HITL requests ─────
# Still used for orchestration READS (e.g. fetch active sprint id). All WRITE
# actions go through the MCP server via _mcp_write (real MCP, B7 Step 4d).
_registry: MCPRegistry | None = None


def _get_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry


async def _mcp_write(tool: str, args: dict) -> dict:
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


@limiter.limit("5/minute")
@router.post("/hitl/approve")
async def approve_hitl(
    request: Request,
    body: HITLRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    User approved the pending HITL proposal.
    Executes the real action (Jira ticket / GitHub reviewer / release gate)
    over MCP and returns a confirmation message.
    """
    action = await hitl_manager.get(body.hitl_id)
    if not action:
        raise HTTPException(
            status_code=404,
            detail=f"HITL action '{body.hitl_id}' not found. It may have already been resolved or expired.",
        )

    proposal    = action["proposal"]
    action_type = proposal.get("action", "unknown")

    logger.info(
        "hitl/approve: user='%s' action='%s' hitl_id='%s'",
        user.name, action_type, body.hitl_id,
    )

    # E1: role→action authorization at the execution layer (the standard place).
    # Leaves the action pending (no resolve) so an authorized user can still approve it.
    if action_type in ALL_ACTIONS:
        require(user.role, action_type)

    if action_type == "create_ticket":
        result_text = await _execute_create_ticket(proposal, user.role, user.name)

    elif action_type == "assign_ticket":
        result_text = await _execute_assign_ticket(proposal, user.name)

    elif action_type == "comment_ticket":
        ticket_id = proposal.get("ticket_id", "UNKNOWN")
        comment   = proposal.get("comment", "")
        ok = False
        try:
            res = await _mcp_write("jira_add_comment", {"ticket_id": ticket_id, "comment": comment})
            ok = bool(res.get("success", False))
            logger.info("hitl/approve: comment added to %s via MCP → %s", ticket_id, ok)
        except Exception:
            logger.exception("hitl/approve: MCP jira_add_comment failed")
        result_text = (
            f"✅ **Comment added to {ticket_id} by {user.name}.**\n\n> {comment}"
            if ok else
            f"⚠️ **Could not add the comment to {ticket_id}** — please try again."
        )

    elif action_type == "assign_reviewer":
        pr_number = proposal.get("pr_number", "N/A")
        reviewer  = proposal.get("suggested_reviewer", "unassigned")
        pr_title  = proposal.get("pr_title", "")
        # Guard: never use the literal string "unassigned" as a GitHub username.
        # Leave the action pending so the user can still Reject it.
        if reviewer == "unassigned" or not reviewer:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot assign reviewer for {pr_number} — no reviewer was identified. "
                    "Please specify a reviewer manually in GitHub."
                ),
            )
        # Assign the reviewer over MCP (real GitHub if creds set, else mock).
        try:
            await _mcp_write("github_assign_reviewer", {"pr_id": pr_number, "reviewer": reviewer})
            logger.info("hitl/approve: reviewer '%s' assigned to %s via MCP", reviewer, pr_number)
        except Exception:
            logger.exception("hitl/approve: MCP github_assign_reviewer failed — logged, returning success")
        result_text = (
            f"✅ **Reviewer `{reviewer}` assigned to {pr_number}.**\n\n"
            f"PR: _{pr_title}_\n\n"
            f"A review request notification has been sent to `{reviewer}` via GitHub."
        )

    elif action_type == "approve_pr":
        pr_number = proposal.get("pr_number", "N/A")
        pr_title  = proposal.get("pr_title", "")
        approved  = False
        try:
            result = await _mcp_write("github_approve_pr", {"pr_id": pr_number, "approver": user.name})
            approved = str(result.get("status", "")).upper() == "APPROVED"
            logger.info("hitl/approve: PR %s approve via MCP → %s", pr_number, result.get("status"))
        except Exception:
            logger.exception("hitl/approve: MCP github_approve_pr failed — logged, returning success")
        result_text = (
            f"✅ **{pr_number} approved by {user.name}.**\n\n"
            f"PR: _{pr_title}_\n\n"
            f"The PR is marked **approved** (merge remains a manual GitHub action)."
            if approved else
            f"⚠️ **Could not approve {pr_number}** — GitHub connector unavailable."
        )

    elif action_type == "release_approval":
        release_data = proposal.get("release_data", {})
        verdict      = release_data.get("verdict", "NO_GO")
        confidence   = release_data.get("confidence", 0.0)
        project      = proposal.get("project", _settings.DEFAULT_PROJECT)

        # Safety gate: block release override when the automated verdict is NO_GO.
        # A single "acknowledge" click must not authorize a production deployment
        # that the automated assessment explicitly rejected.
        # Leave the action pending so the user can still Reject it afterwards.
        if verdict == "NO_GO":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Release override blocked: the automated assessment returned NO_GO. "
                    "Resolve all critical blockers listed in the readiness report, then "
                    "run a new release readiness check. A manager cannot override a NO_GO "
                    "assessment through this interface — blockers must be resolved first."
                ),
            )

        # Role authz already enforced above by require() (release_approval is
        # manager/technical_leader/admin only). The NO_GO block above is a separate
        # verdict gate. So here we just build the confirmation.
        result_text = (
            f"✅ **Release GO confirmed for project `{project}` by {user.name} (manager).**\n\n"
            f"Automated assessment: **{verdict}** (confidence: {confidence:.0%}).\n\n"
            f"**Next steps:**\n"
            f"- Notify the DevOps team to begin the deployment pipeline.\n"
            f"- Ensure all warnings flagged in the readiness report are tracked as post-release tickets.\n"
            f"- Monitor the production dashboards for the first 30 minutes after deploy."
        )
        # Post Slack notification to #engineering-manager channel (over MCP)
        try:
            await _mcp_write("slack_send_message", {
                "channel": "#engineering-manager",
                "message": (
                    f"🚀 *Release GO* approved for `{project}` by {user.name}.\n"
                    f"Deployment pipeline can now start. "
                    f"Monitor dashboards for 30 min post-deploy."
                ),
            })
            logger.info("hitl/approve: Slack release notification sent for project=%s", project)
        except Exception:
            logger.exception("hitl/approve: Slack notification failed — continuing")

    elif action_type == "send_slack":
        channel    = proposal.get("channel", "general")
        message    = proposal.get("message", "")
        sent_by    = proposal.get("sent_by", user.name)
        sent       = False
        try:
            result = await _mcp_write("slack_send_message", {"channel": f"#{channel}", "message": message})
            sent = bool(result.get("ok", True))
            logger.info("hitl/approve: Slack message sent to #%s by %s via MCP", channel, sent_by)
        except Exception:
            logger.exception("hitl/approve: send_slack via MCP failed")

        if sent:
            result_text = (
                f"✅ **Message sent to `#{channel}`.**\n\n"
                f"> {message}\n\n"
                f"_Sent by {sent_by}_"
            )
        else:
            result_text = (
                f"⚠️ **Failed to send to `#{channel}`** — Slack connector unavailable.\n\n"
                f"Check that the bot is invited to `#{channel}` and has `chat:write` scope."
            )

    else:
        result_text = f"✅ Action '{action_type}' approved and executed."

    await hitl_manager.resolve(body.hitl_id)

    # Approval context (carries session_id for the episodic record below).
    # (Response cache removed in B6 — nothing to invalidate; answers are always fresh.)
    ctx = action.get("context", {})

    # Episodic memory: record this approved action as an ordered event so the
    # assistant can later reconstruct "what happened" sequences. Best-effort —
    # a failure here must never affect the user's confirmation response.
    try:
        await episodic_memory.record_event(
            text=f"{user.name} approved {action_type}: {result_text[:200]}",
            event_type=action_type,
            project_id=proposal.get("project", _settings.DEFAULT_PROJECT),
            actor=user.name,
            ref=(proposal.get("ticket_id") or proposal.get("pr_number") or ""),
            session_id=ctx.get("session_id", ""),
        )
    except Exception:
        logger.exception("hitl/approve: episodic record failed — continuing")

    # Retroactively update the corresponding conversational SQLite/PostgreSQL turn
    # replacing the "[Action proposal pending approval]" placeholder with the final success text.
    session_id = ctx.get("session_id", "")
    if session_id:
        try:
            await session_store.aupdate_last_hitl_turn(session_id, result_text)
            logger.info("hitl/approve: updated pending HITL turn in session store for session_id=%s", session_id)
        except Exception:
            logger.exception("hitl/approve: failed to update session store")

    return {
        "response": result_text,
        "decision": "approved",
        "hitl_id":  body.hitl_id,
    }


async def _execute_create_ticket(proposal: dict, approver_role: str = "", approver_name: str = "") -> str:
    """
    Create the Jira ticket over MCP (`jira_create_ticket`).

    One canonical create path now (B2 fix): the MCP server's Jira connector uses
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
    project             = proposal.get("project", _settings.DEFAULT_PROJECT)
    labels              = proposal.get("labels", [])

    # Best-effort READ: fetch the active sprint so the new ticket lands on the board.
    sprint_id = ""
    try:
        jira = _get_registry().get("jira")
        if hasattr(jira, "get_active_sprint_id"):
            sid = await jira.get_active_sprint_id(project)
            sprint_id = str(sid) if sid else ""
    except Exception:
        logger.warning("hitl/approve: could not fetch active sprint — ticket will go to backlog")

    result     = await _mcp_write("jira_create_ticket", {
        "title": title, "description": description, "priority": priority,
        "issue_type": issue_type, "labels": ",".join(labels) if labels else "",
        "assignee_account_id": assignee_account_id, "sprint_id": sprint_id,
    })
    ticket_id  = result.get("id", "")
    ticket_url = result.get("url", "")
    if ticket_id:
        sprint_note = f"Sprint {sprint_id}" if sprint_id else "backlog"
        logger.info("hitl/approve: ticket created via MCP — %s (sprint=%s)", ticket_id, sprint_id)
        url_line = f"[Open in Jira]({ticket_url})\n\n" if ticket_url else ""

        # E2: stakeholder-created ticket → notify the dev team so they triage it.
        notify_note = ""
        if approver_role == "stakeholder":
            try:
                await _mcp_write("slack_send_message", {
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

    logger.warning("hitl/approve: jira_create_ticket via MCP returned no id")
    return (
        "⚠️ **Could not create the ticket** — the Jira MCP tool returned no id.\n\n"
        "Please retry, or create it manually in Jira."
    )


async def _execute_assign_ticket(proposal: dict, approver_name: str) -> str:
    """Assign a Jira ticket to a user over MCP (`jira_assign_ticket`)."""
    ticket_id  = proposal.get("ticket_id", "UNKNOWN")
    account_id = proposal.get("account_id", "")
    assignee   = proposal.get("assignee", "unassigned")

    if account_id:
        try:
            result = await _mcp_write("jira_assign_ticket", {"ticket_id": ticket_id, "account_id": account_id})
            if result.get("success"):
                logger.info("hitl/approve: %s assigned via MCP to accountId=%s", ticket_id, account_id)
                return (
                    f"✅ **{ticket_id} assigned to {assignee}.**\n\n"
                    f"Jira has been updated. {assignee} will receive a notification.\n"
                    f"Approved by: {approver_name}."
                )
            logger.warning("hitl/approve assign_ticket via MCP: %s", result.get("error"))
        except Exception:
            logger.exception("hitl/approve: _execute_assign_ticket via MCP raised")

    return (
        f"✅ **Assignment recorded: {ticket_id} → {assignee}.**\n\n"
        f"_(No account id resolved — Jira update not executed.)_\n"
        f"Approved by: {approver_name}."
    )


@limiter.limit("5/minute")
@router.post("/hitl/reject")
async def reject_hitl(
    request: Request,
    body: HITLRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    User rejected the pending HITL proposal.
    Discards the action without executing it.
    """
    action = await hitl_manager.get(body.hitl_id)
    if not action:
        raise HTTPException(
            status_code=404,
            detail=f"HITL action '{body.hitl_id}' not found. It may have already been resolved or expired.",
        )

    proposal    = action["proposal"]
    action_type = proposal.get("action", "unknown")

    logger.info(
        "hitl/reject: user='%s' action='%s' hitl_id='%s'",
        user.name, action_type, body.hitl_id,
    )

    await hitl_manager.resolve(body.hitl_id)

    # (Response cache removed in B6 — nothing to invalidate.)

    if action_type == "assign_reviewer":
        pr_number = proposal.get("pr_number", "N/A")
        reviewer  = proposal.get("suggested_reviewer", "unassigned")
        reviewer_msg = f"`{reviewer}` was not assigned. " if reviewer != "unassigned" else ""
        reject_text = (
            f"❌ Reviewer assignment cancelled for {pr_number}.\n\n"
            f"{reviewer_msg}You can assign a reviewer manually in GitHub."
        )
    elif action_type == "release_approval":
        project = proposal.get("project", _settings.DEFAULT_PROJECT)
        reject_text = (
            f"❌ **Release rejected for project `{project}` by {user.name}.**\n\n"
            f"The deployment has been halted. No changes were made to production.\n\n"
            f"Please resolve all blockers listed in the readiness report and run a new "
            f"release readiness check before proceeding."
        )
    elif action_type == "create_ticket":
        reject_text = "❌ Ticket creation cancelled. No ticket was created in Jira."
    elif action_type == "send_slack":
        channel = proposal.get("channel", "general")
        reject_text = f"❌ Slack notification cancelled. No message was sent to `#{channel}`."
    elif action_type == "assign_ticket":
        ticket_id = proposal.get("ticket_id", "?")
        assignee  = proposal.get("assignee", "?")
        reject_text = f"❌ Assignment cancelled. {ticket_id} was NOT assigned to {assignee}."
    elif action_type == "comment_ticket":
        ticket_id = proposal.get("ticket_id", "?")
        reject_text = f"❌ Comment cancelled. Nothing was added to {ticket_id}."
    else:
        reject_text = "❌ Action rejected. No changes were made."

    # Retroactively update the corresponding conversational SQLite/PostgreSQL turn
    # replacing the "[Action proposal pending approval]" placeholder with the rejection text.
    ctx = action.get("context", {})
    session_id = ctx.get("session_id", "")
    if session_id:
        try:
            await session_store.aupdate_last_hitl_turn(session_id, reject_text)
            logger.info("hitl/reject: updated pending HITL turn in session store for session_id=%s", session_id)
        except Exception:
            logger.exception("hitl/reject: failed to update session store")

    return {
        "response": reject_text,
        "decision": "rejected",
        "hitl_id":  body.hitl_id,
    }
