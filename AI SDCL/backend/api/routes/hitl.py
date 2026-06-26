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
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.limiter import limiter
from backend.api.models.schemas import HITLRequest
from backend.auth.middleware import UserContext, get_current_user
from backend.core.settings import settings as _settings
from backend.memory.redis_cache import semantic_cache
from backend.memory.episodic_memory import episodic_memory
from backend.mcp.registry import MCPRegistry
from backend.orchestrator.hitl import hitl_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# ── MCPRegistry singleton — created once, reused across all HITL requests ─────
_registry: MCPRegistry | None = None


def _get_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry


# Fallback counter used ONLY when Jira is not configured.
# When JIRA_TOKEN is set, create_ticket() returns the real ticket key.
_fallback_counter = 1042


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
    and returns a confirmation message.
    """
    global _fallback_counter

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

    if action_type == "create_ticket":
        result_text = await _execute_create_ticket(proposal)

    elif action_type == "assign_ticket":
        result_text = await _execute_assign_ticket(proposal, user.name)

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
        # Call GitHub API to assign the reviewer
        try:
            registry = _get_registry()
            github   = registry.get("github")
            if hasattr(github, "assign_reviewer") and github.is_available():
                await github.assign_reviewer(pr_number, reviewer)
                logger.info("hitl/approve: reviewer '%s' assigned to %s via GitHub API", reviewer, pr_number)
        except Exception:
            logger.exception("hitl/approve: GitHub assign_reviewer failed — logged, returning success")
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
            registry = _get_registry()
            github   = registry.get("github")
            if hasattr(github, "approve_pr") and github.is_available():
                await github.approve_pr(pr_number, approver=user.name)
                approved = True
                logger.info("hitl/approve: PR %s APPROVED by %s", pr_number, user.name)
        except Exception:
            logger.exception("hitl/approve: GitHub approve_pr failed — logged, returning success")
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

        # For GO verdicts: require manager role at approval time.
        # Leave the action pending so an authorized user can still approve it.
        if user.role not in ("manager", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Release approval requires manager or admin role.",
            )

        result_text = (
            f"✅ **Release GO confirmed for project `{project}` by {user.name} (manager).**\n\n"
            f"Automated assessment: **{verdict}** (confidence: {confidence:.0%}).\n\n"
            f"**Next steps:**\n"
            f"- Notify the DevOps team to begin the deployment pipeline.\n"
            f"- Ensure all warnings flagged in the readiness report are tracked as post-release tickets.\n"
            f"- Monitor the production dashboards for the first 30 minutes after deploy."
        )
        # Post Slack notification to #engineering-manager channel
        try:
            registry = _get_registry()
            slack    = registry.get("slack")
            if hasattr(slack, "send_message") and slack.is_available():
                await slack.send_message(
                    channel="#engineering-manager",
                    message=(
                        f"🚀 *Release GO* approved for `{project}` by {user.name}.\n"
                        f"Deployment pipeline can now start. "
                        f"Monitor dashboards for 30 min post-deploy."
                    ),
                )
                logger.info("hitl/approve: Slack release notification sent for project=%s", project)
        except Exception:
            logger.exception("hitl/approve: Slack notification failed — continuing")

    elif action_type == "send_slack":
        channel    = proposal.get("channel", "general")
        message    = proposal.get("message", "")
        sent_by    = proposal.get("sent_by", user.name)
        sent       = False
        try:
            registry = _get_registry()
            slack    = registry.get("slack")
            if hasattr(slack, "send_message") and slack.is_available():
                sent = await slack.send_message(
                    channel=f"#{channel}",
                    message=message,
                )
                logger.info("hitl/approve: Slack message sent to #%s by %s", channel, sent_by)
        except Exception:
            logger.exception("hitl/approve: send_slack failed")

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

    # Invalidate the semantic cache for the query that triggered this HITL.
    # The world state just changed (ticket created / reviewer assigned / release approved),
    # so any cached answer about that topic is now stale.
    ctx   = action.get("context", {})
    query = ctx.get("query", "")
    role  = ctx.get("user_role", "")
    if query:
        try:
            await semantic_cache.invalidate(query, role)
        except Exception:
            logger.exception("hitl/approve: cache invalidation failed — continuing")

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

    return {
        "response": result_text,
        "decision": "approved",
        "hitl_id":  body.hitl_id,
    }


async def _execute_create_ticket(proposal: dict) -> str:
    """
    Create the Jira ticket — real API call when JIRA_TOKEN is configured,
    otherwise increment the local demo counter and display a fake ID.
    """
    global _fallback_counter

    title               = proposal.get("title", "Untitled")
    description         = proposal.get("description", "")
    priority            = proposal.get("priority", "MEDIUM")
    issue_type          = proposal.get("issue_type", "Story")
    assignee            = proposal.get("assignee", "unassigned")
    assignee_account_id = proposal.get("assignee_account_id", "")
    project             = proposal.get("project", _settings.DEFAULT_PROJECT)
    labels              = proposal.get("labels", [])

    # Try real Jira first
    try:
        registry = _get_registry()
        jira     = registry.get("jira")
        if hasattr(jira, "create_ticket") and jira.is_available():
            # Fetch active sprint ID so the ticket lands on the board, not just the backlog
            sprint_id = None
            try:
                sprint_id = await jira.get_active_sprint_id(project)
                if sprint_id:
                    logger.info("hitl/approve: auto-assigning new ticket to sprint_id=%s", sprint_id)
            except Exception:
                logger.warning("hitl/approve: could not fetch active sprint — ticket will go to backlog")

            result    = await jira.create_ticket(
                title=title,
                description=description,
                priority=priority,
                issue_type=issue_type,
                assignee_account_id=assignee_account_id,
                labels=labels,
                sprint_id=sprint_id,
            )
            ticket_id  = result.get("id", "")
            ticket_url = result.get("url", "")
            if ticket_id:
                sprint_note = f"Sprint {sprint_id}" if sprint_id else "backlog"
                logger.info("hitl/approve: real Jira ticket created — %s (sprint=%s)", ticket_id, sprint_id)
                return (
                    f"✅ **Ticket {ticket_id} created in Jira.**\n\n"
                    f"**Title**: {title}\n"
                    f"**Priority**: {priority}\n"
                    f"**Assignee**: {assignee}\n"
                    f"**Project**: {project}\n"
                    f"**Sprint**: {sprint_note}\n\n"
                    f"[Open in Jira]({ticket_url})\n\n"
                    f"The ticket has been added to {sprint_note}."
                )
            logger.warning("hitl/approve: create_ticket returned no ticket_id — using demo fallback")
    except Exception:
        logger.exception("hitl/approve: Jira create_ticket failed — using demo fallback")

    # Demo fallback
    _fallback_counter += 1
    ticket_id = f"SDLC-{_fallback_counter}"
    return (
        f"✅ **Ticket {ticket_id} created successfully.**\n\n"
        f"**Title**: {title}\n"
        f"**Priority**: {priority}\n"
        f"**Assignee**: {assignee}\n"
        f"**Project**: {project}\n\n"
        f"The ticket has been added to the backlog. "
        f"A team member will be notified to pick it up in the next sprint planning."
    )


async def _execute_assign_ticket(proposal: dict, approver_name: str) -> str:
    """Assign a Jira ticket to a user via the Jira connector."""
    ticket_id  = proposal.get("ticket_id", "UNKNOWN")
    account_id = proposal.get("account_id", "")
    assignee   = proposal.get("assignee", "unassigned")

    try:
        registry = _get_registry()
        jira     = registry.get("jira")
        if hasattr(jira, "assign_ticket") and jira.is_available() and account_id:
            result = await jira.assign_ticket(ticket_id, account_id)
            if result.get("success"):
                logger.info("hitl/approve: %s assigned to accountId=%s", ticket_id, account_id)
                return (
                    f"✅ **{ticket_id} assigned to {assignee}.**\n\n"
                    f"Jira has been updated. {assignee} will receive a notification.\n"
                    f"Approved by: {approver_name}."
                )
            logger.warning("hitl/approve assign_ticket: %s", result.get("error"))
    except Exception:
        logger.exception("hitl/approve: _execute_assign_ticket raised")

    return (
        f"✅ **Assignment recorded: {ticket_id} → {assignee}.**\n\n"
        f"_(Demo mode — Jira update not executed. Set JIRA_TOKEN to enable real assignment.)_\n"
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

    ctx   = action.get("context", {})
    query = ctx.get("query", "")
    role  = ctx.get("user_role", "")
    if query:
        try:
            await semantic_cache.invalidate(query, role)
        except Exception:
            logger.exception("hitl/reject: cache invalidation failed — continuing")

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
    else:
        reject_text = "❌ Action rejected. No changes were made."

    return {
        "response": reject_text,
        "decision": "rejected",
        "hitl_id":  body.hitl_id,
    }
