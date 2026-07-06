"""
backend/api/routes/webhooks.py

Inbound webhook endpoints — GitHub PR events and Slack @mention ticket creation.

POST /webhooks/github
  When a pull request is opened / reopened / marked ready-for-review, GitHub
  sends an event here. We auto-trigger the existing PR Review agent (no human
  has to ask) and post its review to Slack. This implements the assignment
  scenario "Agent asks for human approval for PR" and the solution document's
  GitHub-webhook trigger (Section 8.4).

POST /webhooks/slack
  When a user @mentions the bot in Slack (e.g. "@AIBot ticket: login broken"),
  Slack sends an app_mention event here. We run the full LangGraph pipeline in
  a background task, check for duplicate tickets, and post an Approve/Reject
  Block Kit card back to the Slack thread.

POST /webhooks/slack-action
  Receives Slack interactive button clicks (Approve / Reject on the HITL card).
  Creates the Jira ticket via the existing _execute_create_ticket path, DMs the
  assignee, and posts the result back to the original Slack thread.

Design notes:
  - Both Slack endpoints verify X-Slack-Signature (HMAC-SHA256 + replay guard).
  - All Slack API calls (chat.postMessage, conversations.open) are made directly
    with httpx using SLACK_BOT_TOKEN — the MCP slack_send_message resolves by
    channel name, but Slack events give us raw channel IDs.
  - Slack context (channel_id, thread_ts) is stored alongside the HITL proposal
    in Redis so the action endpoint knows where to post the result.
  - GitHub webhook unchanged — only Slack endpoints are new.
"""
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.parse

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from langchain_core.messages import HumanMessage

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

        # Post the review to Slack (best-effort).
        try:
            from backend.core.config_loader import config
            from backend.mcp_client.client import call_mcp_tool
            raw_channel = config.get_agent("pr_review_agent").get("slack_channel", "slack:#engineering-manager")
            channel = raw_channel.split(":")[-1].lstrip("#")
            await call_mcp_tool("slack_send_message", {
                "channel": channel,
                "message": f"🔎 *Auto PR review — #{pr_number}* ({repo})\n\n{review}",
            })
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


# ─────────────────────────────────────────────────────────────────────────────
# Slack webhook helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slack_sig_valid(body: bytes, timestamp: str, signature: str) -> bool:
    """
    Verify Slack's X-Slack-Signature header (HMAC-SHA256).
    Also rejects replayed requests older than 5 minutes.
    Returns True (and logs a warning) when SLACK_SIGNING_SECRET is not set —
    so local demo mode works without crashing.
    """
    secret = getattr(settings, "SLACK_SIGNING_SECRET", "") or ""
    if not secret:
        logger.warning(
            "webhooks/slack: SLACK_SIGNING_SECRET not set — accepting unverified "
            "request (set the secret in .env to enable signature verification)"
        )
        return True
    try:
        if abs(time.time() - int(timestamp)) > 300:
            logger.warning("webhooks/slack: request timestamp too old — possible replay attack")
            return False
    except (ValueError, TypeError):
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _slack_post(channel: str, *, text: str = "", blocks: list | None = None,
                      thread_ts: str = "") -> None:
    """POST a message to a Slack channel/thread via chat.postMessage."""
    token = getattr(settings, "SLACK_BOT_TOKEN", "") or ""
    if not token:
        logger.warning("webhooks/slack: SLACK_BOT_TOKEN not set — skipping Slack post")
        return
    payload: dict = {"channel": channel}
    if text:
        payload["text"] = text
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10.0,
        )
    if not resp.json().get("ok"):
        logger.warning("webhooks/slack: chat.postMessage failed: %s", resp.text)


def _build_hitl_blocks(proposal: dict, hitl_id: str, final_response: str) -> list:
    """Build the Block Kit card shown in Slack for Approve / Reject."""
    title       = proposal.get("title", "Untitled")
    priority    = proposal.get("priority", "MEDIUM")
    issue_type  = proposal.get("issue_type", "Story")
    assignee    = proposal.get("assignee", "unassigned")
    similar_ref = proposal.get("similar_ref", "")

    summary = (final_response or "")[:2800]  # Slack block text limit is 3000 chars
    similar_note = f"\n>  ⚠️ Similar ticket on record: *{similar_ref}* — review before approving." if similar_ref else ""

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*📋 Ticket Proposal*\n"
                    f"*Title:* {title}\n"
                    f"*Priority:* {priority}  ·  *Type:* {issue_type}  ·  *Assignee:* {assignee}"
                    f"{similar_note}"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve ✓"},
                    "style": "primary",
                    "value": f"approve:{hitl_id}",
                    "action_id": "ticket_approve",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject ✗"},
                    "style": "danger",
                    "value": f"reject:{hitl_id}",
                    "action_id": "ticket_reject",
                },
            ],
        },
    ]


async def _run_ticket_from_slack(text: str, channel_id: str, user_id: str, thread_ts: str) -> None:
    """
    Background task: run the full LangGraph pipeline for a Slack-triggered
    ticket request, then either post the duplicate info or the Approve/Reject
    Block Kit card back to the originating Slack thread.
    """
    try:
        from backend.orchestrator.graph import graph
        from backend.orchestrator.hitl import hitl_manager

        # Strip the bot mention: "<@U123ABC> ticket: ..." → "ticket: ..."
        clean_text = re.sub(r"<@\w+>\s*", "", text).strip()
        if not clean_text:
            await _slack_post(channel_id, text="Please describe the issue after mentioning me.", thread_ts=thread_ts)
            return

        session_id = f"slack-{channel_id}-{thread_ts}"

        state = {
            "messages":             [HumanMessage(content=clean_text)],
            "session_id":           session_id,
            "user_id":              user_id,
            "user_role":            "developer",
            "project_id":           settings.DEFAULT_PROJECT,
            "query":                clean_text,
            "thread_id":            "",
            "intent":               "",
            "agents_to_run":        [],
            "tokens_budget":        8000,
            "tokens_used":          0,
            "conversation_summary": "",
            "recent_messages":      [],
            "semantic_context":     [],
            "agent_payloads":       [],
            "mcp_outputs":          {},
            "rag_chunks":           [],
            "rag_confidence":       0.0,
            "hitl_required":        False,
            "hitl_action_id":       "",
            "hitl_proposal":        {},
            "hitl_decision":        None,
            "final_response":       "",
            "response_cached":      False,
            "skip_persona":         False,
            "faithfulness":         0.0,
            "relevancy":            0.0,
        }

        result   = await graph.ainvoke(state)
        hitl_id  = result.get("hitl_action_id", "")

        if not hitl_id:
            # Duplicate found or the agent decided no ticket is needed — post the message as-is.
            msg = result.get("final_response") or "I couldn't process that request. Please try again."
            await _slack_post(channel_id, text=msg, thread_ts=thread_ts)
            return

        # Amend the Redis context with the Slack coordinates so the action
        # endpoint knows where to post the result when the user clicks Approve.
        action = await hitl_manager.get(hitl_id)
        if action:
            action["context"]["slack_channel"]   = channel_id
            action["context"]["slack_thread_ts"] = thread_ts
            action["context"]["slack_user_id"]   = user_id
            # Re-persist the amended context (same TTL).
            try:
                import redis.asyncio as aioredis
                r: aioredis.Redis = aioredis.from_url(settings.REDIS_URL)
                await r.set(
                    f"hitl:{hitl_id}",
                    json.dumps(action),
                    ex=getattr(settings, "HITL_TTL_SECONDS", 86400),
                )
                await r.aclose()
            except Exception:
                logger.exception("webhooks/slack: failed to amend Redis context for hitl_id=%s", hitl_id)

        proposal = result.get("hitl_proposal", {})
        blocks   = _build_hitl_blocks(proposal, hitl_id, result.get("final_response", ""))
        await _slack_post(channel_id, blocks=blocks, thread_ts=thread_ts,
                          text=f"Ticket proposal ready — approve or reject below.")

        logger.info("webhooks/slack: HITL card posted for hitl_id=%s in channel=%s", hitl_id, channel_id)

    except Exception:
        logger.exception("webhooks/slack: _run_ticket_from_slack failed")
        await _slack_post(channel_id,
                          text="Something went wrong while processing your request. Please try again.",
                          thread_ts=thread_ts)


async def _handle_slack_decision(hitl_id: str, decision: str, clicker_slack_id: str) -> None:
    """
    Execute approve or reject for a Slack-triggered HITL action, then post the
    result back to the original Slack thread.
    """
    try:
        from backend.api.routes.hitl import _execute_create_ticket
        from backend.orchestrator.hitl import hitl_manager

        action = await hitl_manager.get(hitl_id)
        if not action:
            logger.warning("webhooks/slack-action: hitl_id=%s not found (expired or already resolved)", hitl_id)
            return

        proposal  = action.get("proposal", {})
        ctx       = action.get("context", {})
        channel   = ctx.get("slack_channel", "")
        thread_ts = ctx.get("slack_thread_ts", "")

        if decision == "approve":
            result_text = await _execute_create_ticket(
                proposal,
                approver_role="developer",
                approver_name=clicker_slack_id,
            )
        else:
            result_text = "❌ Ticket creation cancelled."

        await hitl_manager.resolve(hitl_id)

        if channel:
            await _slack_post(channel, text=result_text, thread_ts=thread_ts)

    except Exception:
        logger.exception("webhooks/slack-action: _handle_slack_decision failed for hitl_id=%s", hitl_id)


# ─────────────────────────────────────────────────────────────────────────────
# Slack endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/webhooks/slack")
async def slack_event_webhook(request: Request, background: BackgroundTasks):
    """
    Receive Slack Events API calls (app_mention).
    Returns 200 immediately; real work runs in a background task.
    Also handles the one-time URL verification challenge Slack sends on setup.
    """
    body      = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _slack_sig_valid(body, timestamp, signature):
        logger.warning("webhooks/slack: invalid signature — rejecting")
        return {"status": "rejected", "reason": "invalid signature"}

    try:
        payload = json.loads(body)
    except Exception:
        return {"status": "ignored", "reason": "invalid JSON"}

    # One-time URL verification Slack sends when you register the endpoint.
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    event = payload.get("event", {})

    # Only handle @mentions; ignore bot's own messages to avoid feedback loops.
    if event.get("type") != "app_mention" or event.get("bot_id"):
        return {"status": "ignored"}

    text      = event.get("text", "")
    channel   = event.get("channel", "")
    user_id   = event.get("user", "")
    thread_ts = event.get("thread_ts") or event.get("ts", "")

    background.add_task(_run_ticket_from_slack, text, channel, user_id, thread_ts)
    logger.info("webhooks/slack: accepted app_mention from user=%s in channel=%s", user_id, channel)
    return {"status": "accepted"}


@router.post("/webhooks/slack-action")
async def slack_action_webhook(request: Request, background: BackgroundTasks):
    """
    Receive Slack interactive component payloads (Approve / Reject button clicks).
    Slack sends these as application/x-www-form-urlencoded with a 'payload' key.
    Returns 200 immediately; decision execution runs in a background task.
    """
    body      = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _slack_sig_valid(body, timestamp, signature):
        logger.warning("webhooks/slack-action: invalid signature — rejecting")
        return {"status": "rejected", "reason": "invalid signature"}

    try:
        form    = urllib.parse.parse_qs(body.decode())
        raw     = form.get("payload", [""])[0]
        payload = json.loads(raw) if raw else {}
    except Exception:
        return {"status": "ignored", "reason": "malformed payload"}

    actions = payload.get("actions", [])
    if not actions:
        return {"status": "ignored"}

    action_value    = actions[0].get("value", "")   # "approve:{hitl_id}" or "reject:{hitl_id}"
    clicker_user_id = payload.get("user", {}).get("id", "unknown")

    if ":" not in action_value:
        return {"status": "ignored", "reason": "unrecognised action value"}

    decision, hitl_id = action_value.split(":", 1)
    if decision not in {"approve", "reject"}:
        return {"status": "ignored", "reason": "unrecognised decision"}

    background.add_task(_handle_slack_decision, hitl_id, decision, clicker_user_id)
    logger.info("webhooks/slack-action: %s by user=%s for hitl_id=%s", decision, clicker_user_id, hitl_id)
    return {"status": "accepted"}
