"""
backend/orchestrator/hitl.py

HITLManager — stores pending HITL actions and allows resolve/reject.

Phase 7a: in-memory dict. All pending actions are lost on server restart.
Phase 9:  will move to Redis (save_hitl_state / load_hitl_state) so pending
          actions survive container restarts and can be shared across workers.

Why a separate class instead of writing directly to Redis?
  Keeps the storage implementation swappable. Agents and graph nodes call
  hitl_manager.save() — they never know whether it goes to a dict or Redis.
  Changing Phase 9: one file changes, zero agent/graph code changes.
"""
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)


class HITLManager:
    """
    In-memory store for pending human-approval actions.

    Lifecycle of a HITL action:
      1. Agent sets hitl_required=True, hitl_proposal={...} in state
      2. check_hitl node calls hitl_manager.save() → gets back a hitl_id
      3. Graph returns to FastAPI with hitl_action_id in response
      4. FastAPI returns hitl_action_id to frontend
      5. Frontend shows Approve/Reject buttons
      6. User clicks → POST /api/hitl/approve or /api/hitl/reject
      7. HITL route calls hitl_manager.resolve() → removes from store
      8. HITL route executes (or discards) the action → returns confirmation
    """

    def __init__(self) -> None:
        # hitl_id → {hitl_id, proposal, context, created_at}
        self._pending: dict[str, dict] = {}

    def save(self, proposal: dict, context: dict) -> str:
        """
        Save a pending HITL action and return its unique ID.

        Args:
            proposal: the action to be approved — what the agent wants to do.
                      e.g. {"action": "create_ticket", "title": "...", ...}
            context:  session context needed to execute the action on approval.
                      e.g. {"session_id": "...", "user_role": "...", "project_id": "..."}

        Returns:
            hitl_id — a UUID string the frontend uses to call /api/hitl/approve
        """
        hitl_id = str(uuid.uuid4())
        self._pending[hitl_id] = {
            "hitl_id":    hitl_id,
            "proposal":   proposal,
            "context":    context,
            "created_at": datetime.now().isoformat(),
        }
        logger.info(
            "HITLManager.save: hitl_id='%s' action='%s' (total pending: %d)",
            hitl_id, proposal.get("action", "unknown"), len(self._pending),
        )
        return hitl_id

    def get(self, hitl_id: str) -> dict | None:
        """Return the pending action for a given hitl_id, or None if not found."""
        return self._pending.get(hitl_id)

    def resolve(self, hitl_id: str) -> dict | None:
        """Remove and return the pending action (marks it as handled)."""
        action = self._pending.pop(hitl_id, None)
        if action:
            logger.info("HITLManager.resolve: hitl_id='%s' removed", hitl_id)
        else:
            logger.warning("HITLManager.resolve: hitl_id='%s' not found", hitl_id)
        return action

    def pending_count(self) -> int:
        """Return the number of actions currently awaiting human decision."""
        return len(self._pending)


# ── Module-level singleton — imported by graph.py and hitl route ──────────────
# One shared instance so graph nodes and API routes use the same in-memory store.
hitl_manager = HITLManager()
