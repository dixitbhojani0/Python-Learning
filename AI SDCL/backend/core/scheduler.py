"""
backend/core/scheduler.py

APScheduler job runner for proactive sprint risk scans.

Why a scheduler?
  The RiskAgent normally runs when a user asks "what is the sprint risk?".
  But delivery risk should be surfaced BEFORE someone thinks to ask.
  This scheduler runs the RiskAgent automatically at 5pm every weekday,
  logs the result, and (in a future phase) posts it to Slack.

Why AsyncIOScheduler?
  The rest of the app is async (FastAPI + LangGraph). AsyncIOScheduler
  runs jobs on the same event loop — no threading needed.

Misfire handling:
  If the server was down at 5pm, APScheduler would normally queue the missed
  job and run it immediately on startup. We don't want stale reports.
  misfire_grace_time=60 means: if missed by > 60 seconds, skip it entirely.

How it's started:
  main.py starts the scheduler in its lifespan context (before yield).
  It shuts down after yield (server shutdown). This ensures the scheduler
  is always tied to the server lifecycle.
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.core.config_loader import config

logger = logging.getLogger(__name__)

# ── Module-level scheduler instance ──────────────────────────────────────────
# Created once — started and stopped by main.py lifespan.
scheduler = AsyncIOScheduler()


async def _run_risk_scan():
    """
    Proactive sprint risk scan — runs on schedule, not triggered by a user.

    Builds a synthetic SDLCState for the default project and runs the full
    RiskAgent pipeline. Logs the risk score and blockers.

    Phase 11: replace logger.info with MCP Slack connector to post the
    risk summary to #engineering-manager channel.
    """
    from backend.agents.risk_agent import RiskAgent
    from backend.orchestrator.graph import _get_retriever, _get_provider, _get_registry
    from backend.core.settings import settings

    project = settings.DEFAULT_PROJECT

    logger.info("Scheduler: starting scheduled sprint risk scan for project='%s'", project)

    # Synthetic state — same shape as a real graph state but query is fixed
    synthetic_state = {
        "query":      "What is the current sprint risk and are there any blockers?",
        "project_id": project,
        "user_role":  "manager",
        "session_id": "scheduler",
        "user_id":    "scheduler",
        "intent":     "risk",
        "agents_to_run": ["risk"],
        "messages":   [],
        "thread_id":  "",
        "tokens_budget": 8000,
        "tokens_used": 0,
        "conversation_summary": "",
        "recent_messages": [],
        "semantic_context": [],
        "agent_payloads": [],
        "mcp_outputs": {},
        "rag_chunks": [],
        "rag_confidence": 0.0,
        "hitl_required": False,
        "hitl_action_id": "",
        "hitl_proposal": {},
        "hitl_decision": None,
        "final_response": "",
        "response_cached": False,
    }

    try:
        agent   = RiskAgent(
            retriever=_get_retriever(),
            llm=_get_provider(),
            mcp_registry=_get_registry(),
        )
        payload = await agent.run(synthetic_state)
        risk    = payload.structured.get("risk_data", {})

        logger.info(
            "Scheduler: risk scan complete — score=%s level=%s blocked=%s/%s",
            risk.get("risk_score", "N/A"),
            risk.get("risk_level", "UNKNOWN"),
            risk.get("blocked_count", "?"),
            risk.get("total_tickets", "?"),
        )

        # Phase 11: post to Slack
        # await mcp.get("slack").post_message(
        #     channel="#engineering-manager",
        #     text=payload.structured["final_response"],
        # )

    except Exception:
        logger.exception("Scheduler: sprint risk scan failed")


def start_scheduler():
    """
    Register the sprint risk scan job and start the APScheduler.

    Schedule is read from agents.yaml (risk_agent.scheduled.cron).
    Falls back to "0 17 * * 1-5" (5pm weekdays) if not configured.
    """
    risk_cfg  = config.get_agent("risk_agent").get("scheduled", {})
    cron_expr = risk_cfg.get("cron", "0 17 * * 1-5")   # default: 5pm Mon–Fri

    scheduler.add_job(
        _run_risk_scan,
        trigger=CronTrigger.from_crontab(cron_expr),
        id="sprint_risk_scan",
        replace_existing=True,
        misfire_grace_time=60,      # skip job if missed by > 60s (server was down)
        max_instances=1,            # never run two copies of the same job
    )

    scheduler.start()
    logger.info(
        "Scheduler: started — sprint_risk_scan cron='%s'", cron_expr
    )


def stop_scheduler():
    """Gracefully shut down the scheduler (called from main.py lifespan shutdown)."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler: stopped")
