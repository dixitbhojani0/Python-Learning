"""
backend/auth/permissions.py

Role → action authorization (E1). The single source of truth for "who may do what".

This is the capability matrix from the product spec (developer/manager/stakeholder):
  - View (reads) is allowed for everyone and is not gated here.
  - WRITE actions are gated at the HITL EXECUTION layer (api/routes/hitl.py),
    which is the standard place to enforce authz — not at output/proposal time.

RBAC is deterministic policy by design, so it lives as an explicit matrix (this is
NOT the kind of decision P1 wants made probabilistically — permissions must be fixed
and auditable). Adjust the matrix here; nothing else changes.

Capability matrix (write actions):
  | action            | developer | manager | technical_leader | stakeholder | admin |
  |-------------------|-----------|---------|------------------|-------------|-------|
  | create_ticket     |    ✅     |   ✅    |       ✅         |  ✅ (→notify)|  ✅   |
  | edit_ticket       |    ✅     |   ✅    |       ✅         |     ✕       |  ✅   |
  | assign_ticket     |    ✅     |   ✅    |       ✅         |     ✕       |  ✅   |
  | comment_ticket    |    ✅     |   ✅    |       ✅         |     ✕       |  ✅   |
  | assign_reviewer   |    ✅     |   ✅    |       ✅         |     ✕       |  ✅   |
  | approve_pr        |    ✅     |   ✅    |       ✅         |     ✕       |  ✅   |
  | reject_pr         |    ✅     |   ✅    |       ✅         |     ✕       |  ✅   |
  | release_approval  |    ✕      |   ✅    |       ✅         |     ✕       |  ✅   |
  | send_slack        |    ✅     |   ✅    |       ✅         |  ✅ (on create)| ✅  |

NOTE: delete_ticket and comment_pr were removed from this matrix (2026-07) — neither
had a backing MCP tool (no jira_delete_ticket / github_comment_pr exists on the server)
nor any agent/HITL wiring anywhere. They were pure aspirational entries with zero actual
capability behind them. Re-add only alongside a real MCP tool + agent + hitl.py handler.
"""
from fastapi import HTTPException, status

# Every write action the HITL layer can execute.
ALL_ACTIONS: frozenset[str] = frozenset({
    "create_ticket", "edit_ticket", "assign_ticket", "comment_ticket",
    "assign_reviewer", "approve_pr", "reject_pr",
    "release_approval", "send_slack",
})

# Stakeholders: read everywhere + may create a ticket (which notifies a developer)
# and send notifications. No edit/delete/assign/PR writes.
_STAKEHOLDER = frozenset({"create_ticket", "send_slack"})

# Full team write access, minus release sign-off (a manager/lead decision).
_FULL_NO_RELEASE = ALL_ACTIONS - {"release_approval"}

# role → allowed write actions
_MATRIX: dict[str, frozenset[str]] = {
    "developer":        _FULL_NO_RELEASE,
    "manager":          ALL_ACTIONS,
    "technical_leader": ALL_ACTIONS,
    "admin":            ALL_ACTIONS,
    "stakeholder":      _STAKEHOLDER,
}


def can(role: str, action: str) -> bool:
    """Return True if `role` may perform write `action`. Unknown role/action → False."""
    return action in _MATRIX.get(role, frozenset())


def require(role: str, action: str) -> None:
    """
    Enforce a role→action permission, raising HTTP 403 if denied.

    Called at the HITL execution layer before performing a write.
    """
    if not can(role, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Your role ('{role}') is not permitted to "
                f"{action.replace('_', ' ')}. "
                "Ask a developer or manager to perform this action."
            ),
        )
