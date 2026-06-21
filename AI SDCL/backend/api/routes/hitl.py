"""
backend/api/routes/hitl.py

POST /api/hitl/approve  — user approved the pending agent proposal
POST /api/hitl/reject   — user rejected the pending agent proposal

Both endpoints:
  1. Look up the pending action in HITLManager by hitl_id
  2. Execute (or discard) the action
  3. Remove the action from the store
  4. Return a plain {"response": "...", "decision": "..."}

Why no graph.ainvoke() here?
  For Phase 7a, the "graph resumption" is simulated:
  approve = execute the stored proposal (mock ticket creation, etc.)
  reject  = discard the stored proposal, return a polite refusal

  Full LangGraph interrupt/resume (streaming mid-graph state) is a Phase 9 upgrade.
  For the demo, this two-request pattern achieves the same user experience.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.api.models.schemas import HITLRequest
from backend.auth.middleware import UserContext, get_current_user
from backend.orchestrator.hitl import hitl_manager

logger  = logging.getLogger(__name__)
router  = APIRouter()

# Monotonically increasing fake ticket counter for demo purposes.
# Real implementation calls Jira MCP.create_ticket() in Phase 7b.
_ticket_counter = 1042


@router.post("/hitl/approve")
async def approve_hitl(
    body: HITLRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    User approved the pending HITL proposal.
    Execute the action (mock for now) and return confirmation.
    """
    global _ticket_counter

    action = hitl_manager.get(body.hitl_id)
    if not action:
        raise HTTPException(
            status_code=404,
            detail=f"HITL action '{body.hitl_id}' not found. It may have already been resolved or expired.",
        )

    proposal     = action["proposal"]
    action_type  = proposal.get("action", "unknown")

    logger.info(
        "hitl/approve: user='%s' action='%s' hitl_id='%s'",
        user.name, action_type, body.hitl_id,
    )

    if action_type == "create_ticket":
        _ticket_counter += 1
        ticket_id    = f"SDLC-{_ticket_counter}"
        result_text  = (
            f"✅ **Ticket {ticket_id} created successfully.**\n\n"
            f"**Title**: {proposal.get('title', 'N/A')}\n"
            f"**Priority**: {proposal.get('priority', 'MEDIUM')}\n"
            f"**Assignee**: {proposal.get('assignee', 'unassigned')}\n"
            f"**Project**: {proposal.get('project', 'antlog')}\n\n"
            f"The ticket has been added to the backlog. "
            f"A team member will be notified to pick it up in the next sprint planning."
        )
    elif action_type == "release_approval":
        release_data = proposal.get("release_data", {})
        verdict      = release_data.get("verdict", "NO_GO")
        project      = proposal.get("project", "antlog")
        result_text  = (
            f"✅ **Release approved for project `{project}` by {user.name}.**\n\n"
            f"Automated assessment was **{verdict}**. Your approval overrides the assessment "
            f"and authorises the deployment to proceed.\n\n"
            f"**Next steps:**\n"
            f"- Notify the DevOps team to begin the deployment pipeline.\n"
            f"- Ensure all blockers flagged in the readiness report are tracked as post-release tickets.\n"
            f"- Monitor the production dashboards for the first 30 minutes after deploy."
        )
    else:
        result_text = f"✅ Action '{action_type}' approved and executed."

    hitl_manager.resolve(body.hitl_id)

    return {
        "response":  result_text,
        "decision":  "approved",
        "hitl_id":   body.hitl_id,
    }


@router.post("/hitl/reject")
async def reject_hitl(
    body: HITLRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    User rejected the pending HITL proposal.
    Discard the action without executing it.
    """
    action = hitl_manager.get(body.hitl_id)
    if not action:
        raise HTTPException(
            status_code=404,
            detail=f"HITL action '{body.hitl_id}' not found. It may have already been resolved or expired.",
        )

    proposal = action["proposal"]

    logger.info(
        "hitl/reject: user='%s' action='%s' hitl_id='%s'",
        user.name, proposal.get("action", "unknown"), body.hitl_id,
    )

    action_type  = proposal.get("action", "unknown")
    hitl_manager.resolve(body.hitl_id)

    if action_type == "release_approval":
        project = proposal.get("project", "antlog")
        reject_text = (
            f"❌ **Release rejected for project `{project}` by {user.name}.**\n\n"
            f"The deployment has been halted. No changes were made to production.\n\n"
            f"Please resolve all blockers listed in the readiness report and run a new "
            f"release readiness check before proceeding."
        )
    elif action_type == "create_ticket":
        reject_text = "❌ Ticket creation cancelled. No ticket was created in Jira."
    else:
        reject_text = "❌ Action rejected. No changes were made."

    return {
        "response": reject_text,
        "decision": "rejected",
        "hitl_id":  body.hitl_id,
    }
