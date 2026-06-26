"""
backend/api/routes/webhooks.py

Inbound webhook endpoints — currently GitHub PR events.

POST /webhooks/github
  When a pull request is opened / reopened / marked ready-for-review, GitHub
  sends an event here. We auto-trigger the existing PR Review agent (no human
  has to ask) and post its review to Slack. This implements the assignment
  scenario "Agent asks for human approval for PR" and the solution document's
  GitHub-webhook trigger (Section 8.4).

Design notes:
  - Signature is verified with HMAC-SHA256 against GITHUB_WEBHOOK_SECRET when
    that secret is configured. If it is not set (local demo), we log a warning
    and accept — never crash.
  - The agent run happens in a FastAPI BackgroundTask so we return 200 fast
    (GitHub times out webhooks after ~10s). The agent path is the SAME one the
    graph uses (_run_agent), so behaviour is identical to a chat-triggered review.
  - Reuses the existing PRReviewAgent + MCP registry — zero new business logic.
"""
import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Request

from backend.core.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Actions that should trigger a review. Edits/labels/syncs are ignored.
_TRIGGER_ACTIONS = {"opened", "reopened", "ready_for_review"}


def _webhook_secret() -> str:
    """Read the webhook secret defensively — absence must never break startup."""
    return getattr(settings, "GITHUB_WEBHOOK_SECRET", "") or ""


def _signature_valid(body: bytes, signature_header: str) -> bool:
    """
    Verify GitHub's X-Hub-Signature-256 header.
    Returns True when no secret is configured (demo mode) after logging a warning.
    """
    secret = _webhook_secret()
    if not secret:
        logger.warning(
            "webhooks/github: GITHUB_WEBHOOK_SECRET not set — accepting unverified "
            "webhook (set the secret in .env to enable signature verification)"
        )
        return True
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def _run_pr_review_and_notify(pr_number: int, pr_title: str, repo: str) -> None:
    """
    Background task: run the PR Review agent for the opened PR, then post its
    review to Slack. Best-effort — any failure is logged, never raised.
    """
    try:
        # Imported lazily so the route module stays import-light and avoids any
        # circular import with the orchestrator at startup.
        from backend.agents.pr_review_agent import PRReviewAgent
        from backend.orchestrator.nodes import _run_agent

        state = {
            "query":           f"Review PR #{pr_number}: {pr_title}",
            "project_id":      settings.DEFAULT_PROJECT,
            "user_role":       "developer",
            "recent_messages": [],
        }
        result   = await _run_agent(PRReviewAgent, state)
        review   = result.get("final_response", "") or f"PR #{pr_number} reviewed."

        logger.info("webhooks/github: auto-review complete for PR #%s in %s", pr_number, repo)

        # Post the review to Slack (best-effort). Mock connector just logs it.
        try:
            from backend.mcp.registry import MCPRegistry
            slack = MCPRegistry().get("slack")
            if hasattr(slack, "send_message") and slack.is_available():
                await slack.send_message(
                    channel="engineering",
                    message=f"🔎 *Auto PR review — #{pr_number}* ({repo})\n\n{review}",
                )
        except Exception:
            logger.exception("webhooks/github: Slack post failed — review still logged")
    except Exception:
        logger.exception("webhooks/github: PR auto-review failed for PR #%s", pr_number)


@router.post("/webhooks/github")
async def github_webhook(request: Request, background: BackgroundTasks):
    """Receive a GitHub webhook, verify it, and auto-trigger PR review on PR-open events."""
    body      = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    event     = request.headers.get("X-GitHub-Event", "")

    if not _signature_valid(body, signature):
        logger.warning("webhooks/github: invalid signature — rejecting")
        # 202-style soft reject: don't leak detail, don't 500.
        return {"status": "rejected", "reason": "invalid signature"}

    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid JSON"}

    if event != "pull_request":
        return {"status": "ignored", "event": event}

    action = payload.get("action", "")
    if action not in _TRIGGER_ACTIONS:
        return {"status": "ignored", "action": action}

    pr        = payload.get("pull_request", {}) or {}
    pr_number = payload.get("number") or pr.get("number", 0)
    pr_title  = pr.get("title", "")
    repo      = (payload.get("repository", {}) or {}).get("full_name", "")

    # Schedule the review AFTER the response is returned so GitHub gets a fast 200.
    background.add_task(_run_pr_review_and_notify, pr_number, pr_title, repo)
    logger.info("webhooks/github: accepted PR #%s (%s) action=%s — review scheduled", pr_number, repo, action)

    return {"status": "accepted", "pr_number": pr_number, "action": action}
