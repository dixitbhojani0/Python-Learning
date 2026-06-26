"""
backend/orchestrator/hitl.py

HITLManager — stores pending HITL actions in Redis with in-memory fallback.

Production behaviour:
  - Redis is available  → actions stored with 24-hour TTL, survive server restarts,
    shared across multiple workers / pods.
  - Redis is unavailable → falls back to an in-memory dict (same as the old Phase 7a
    behaviour) so the demo keeps working even without Docker running.

Key format: hitl:{hitl_id}  (JSON-serialised action dict)
TTL:        HITL_TTL_SECONDS from settings (default 86400 = 24 hours)

All public methods are async because:
  - redis.asyncio requires await for every operation
  - graph nodes that call save() are already async
  - FastAPI routes that call get()/resolve() are already async
"""
import json
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)


class HITLManager:
    """
    Redis-backed store for pending human-approval actions.

    Lifecycle of a HITL action:
      1. Agent sets hitl_required=True, hitl_proposal={...} in state
      2. check_hitl node calls await hitl_manager.save() → gets back a hitl_id
      3. Graph returns to FastAPI with hitl_action_id in response
      4. FastAPI returns hitl_action_id to frontend
      5. Frontend shows Approve / Reject buttons
      6. User clicks → POST /api/hitl/approve or /api/hitl/reject
      7. HITL route calls await hitl_manager.resolve() → removes from store
      8. HITL route executes (or discards) the action → returns confirmation
    """

    def __init__(self) -> None:
        self._redis = None                   # created lazily on first use
        self._fallback: dict[str, dict] = {}  # in-memory safety net
        self._ttl: int = 86400               # 24 h; overridden from settings at first use

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_redis(self):
        """
        Return a live Redis client, or None if Redis is unavailable.
        Creates the connection on first call and caches it.
        Falls back gracefully — one log warning, then silent from then on.
        """
        if self._redis is not None:
            return self._redis
        try:
            from redis.asyncio import Redis
            from backend.core.settings import settings
            self._ttl   = getattr(settings, "HITL_TTL_SECONDS", 86400)
            self._redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            await self._redis.ping()
            logger.info("HITLManager: connected to Redis — actions will survive restarts")
        except Exception as exc:
            logger.warning(
                "HITLManager: Redis unavailable (%s) — using in-memory fallback "
                "(pending actions will be lost on restart)", exc,
            )
            self._redis = None
        return self._redis

    def _key(self, hitl_id: str) -> str:
        return f"hitl:{hitl_id}"

    # ── Public API (all async) ────────────────────────────────────────────────

    async def save(self, proposal: dict, context: dict) -> str:
        """
        Save a pending HITL action and return its unique ID.

        Args:
            proposal: the action the agent wants to take.
                      e.g. {"action": "create_ticket", "title": "...", ...}
            context:  session context stored alongside the proposal.
                      e.g. {"session_id": "...", "user_role": "...", "project_id": "..."}

        Returns:
            hitl_id — a UUID string the frontend uses to call /api/hitl/approve
        """
        hitl_id = str(uuid.uuid4())
        action = {
            "hitl_id":    hitl_id,
            "proposal":   proposal,
            "context":    context,
            "created_at": datetime.now().isoformat(),
        }

        redis = await self._get_redis()
        if redis:
            try:
                await redis.set(self._key(hitl_id), json.dumps(action), ex=self._ttl)
                logger.info(
                    "HITLManager.save: hitl_id='%s' action='%s' → Redis (ttl=%ds)",
                    hitl_id, proposal.get("action", "unknown"), self._ttl,
                )
                return hitl_id
            except Exception:
                logger.exception("HITLManager.save: Redis write failed — using fallback")

        # Fallback: in-memory dict
        self._fallback[hitl_id] = action
        logger.info(
            "HITLManager.save: hitl_id='%s' action='%s' → in-memory fallback",
            hitl_id, proposal.get("action", "unknown"),
        )
        return hitl_id

    async def get(self, hitl_id: str) -> dict | None:
        """Return the pending action for a given hitl_id, or None if not found / expired."""
        redis = await self._get_redis()
        if redis:
            try:
                raw = await redis.get(self._key(hitl_id))
                if raw:
                    return json.loads(raw)
                # Not in Redis — check fallback (edge case: Redis reconnected after outage)
            except Exception:
                logger.exception("HITLManager.get: Redis read failed — checking fallback")

        return self._fallback.get(hitl_id)

    async def resolve(self, hitl_id: str) -> dict | None:
        """
        Remove and return the pending action (marks it as handled).
        Called by both approve and reject endpoints.
        """
        redis = await self._get_redis()
        if redis:
            try:
                raw    = await redis.get(self._key(hitl_id))
                action = json.loads(raw) if raw else None
                await redis.delete(self._key(hitl_id))
                if action:
                    logger.info("HITLManager.resolve: hitl_id='%s' removed from Redis", hitl_id)
                    return action
            except Exception:
                logger.exception("HITLManager.resolve: Redis operation failed — checking fallback")

        # Fallback
        action = self._fallback.pop(hitl_id, None)
        if action:
            logger.info("HITLManager.resolve: hitl_id='%s' removed from in-memory fallback", hitl_id)
        else:
            logger.warning("HITLManager.resolve: hitl_id='%s' not found anywhere", hitl_id)
        return action

    async def pending_count(self) -> int:
        """Return the number of actions currently awaiting human decision."""
        redis = await self._get_redis()
        if redis:
            try:
                keys = await redis.keys("hitl:*")
                return len(keys)
            except Exception:
                pass
        return len(self._fallback)


# ── Module-level singleton — imported by graph.py and hitl route ──────────────
hitl_manager = HITLManager()
