"""
backend/agents/notify_agent.py

Notify Agent — sends a Slack message from the chatbot.

Flow:
  1. LLM extracts {channel, message, intent} from the user query (agentic — the
     LLM owns intent, not a hardcoded keyword list). An explicit "#channel" token
     in the raw query overrides the extracted channel since it is unambiguous.
  2. If the intent is a sprint status with no explicit body, the agent composes
     one from live MCP sprint-board + PR data.
  3. Surface a HITL preview; on approve, send via slack.send_message().

Example queries (any phrasing works — the LLM interprets, no fixed patterns):
  "notify the managers that the sprint is at risk"
  "send sprint status to #engineering-manager"
  "notify #backend: PR-5 needs review"
"""
import asyncio
import logging
import re

try:
    from langsmith import traceable
except ImportError:
    def traceable(fn=None, **_kw):
        return fn if fn is not None else (lambda f: f)

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.core.settings import settings as _settings
from backend.mcp_client.client import call_mcp_tool
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)

# Default channel comes from config/agents.yaml notify_agent.slack_channel (single
# source of truth). Hardcoded "general" is the last-resort fallback only.
_FALLBACK_CHANNEL = "general"


def _format_sprint_status(board: dict, prs: list[dict]) -> str:
    """Build a concise Slack-ready sprint status from live board + PR data."""
    sprint      = board.get("sprint", "Current sprint")
    pct         = board.get("completion_pct", "?")
    done        = board.get("done", "?")
    total       = board.get("total_tickets", "?")
    days        = board.get("days_remaining", "?")
    goal        = board.get("goal", "")
    risk        = board.get("risk_level", "")
    blocked_ids = board.get("blocked_tickets", [])

    lines = [
        f"*{sprint} status* — {pct}% complete ({done}/{total} tickets done), "
        f"{days} days remaining."
    ]
    if goal:
        lines.append(f"Goal: {goal}")

    progress = [
        f"{board[key]} {label}"
        for label, key in (
            ("done", "done"), ("in progress", "in_progress"),
            ("blocked", "blocked"), ("not started", "not_started"),
        )
        if board.get(key) is not None
    ]
    if progress:
        lines.append("Progress: " + " · ".join(progress) + ".")
    if risk:
        lines.append(f"Risk level: {risk}.")
    if blocked_ids:
        lines.append(f"Blocked: {', '.join(blocked_ids)}.")
    lines.append(f"{len(prs)} open PR(s) awaiting review.")

    return "\n".join(lines)


class NotifyAgent(BaseAgent):
    """
    Sends a Slack message via the MCP Slack connector.
    No HITL — sending a notification is a low-stakes, reversible action.
    """

    def __init__(self, retriever: HybridRetriever, llm, config_loader=None, mcp_registry=None):
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    def _default_channel(self) -> str:
        """Default Slack channel from agents.yaml notify_agent.slack_channel (strip '#')."""
        configured = (self.config.get_agent("notify_agent") or {}).get("slack_channel", "")
        return configured.lstrip("#") or _FALLBACK_CHANNEL

    async def _extract_request(self, query: str) -> dict:
        """
        LLM-driven extraction of {channel, message, intent} from a notify query.
        Returns {} on parse error / LLM failure so the caller degrades gracefully.
        """
        prompt = self.config.get_prompt(
            "notify_extraction", query=query, default_channel=self._default_channel(),
        )
        if not prompt:
            return {}
        try:
            resp = await self.llm.generate_structured(
                prompt,
                self.config.get_prompt("system_prompt"),
                temperature=0.0,
                max_tokens=200,
            )
            if resp.parse_error or not resp.structured:
                return {}
            return resp.structured
        except Exception:
            logger.exception("NotifyAgent: notify_extraction failed — degrading")
            return {}

    async def _compose_sprint_status(self, project: str) -> str:
        """
        Fetch the live sprint board + open PRs and compose a status message.
        Returns "" if MCP is unavailable or the board can't be fetched, so the
        caller falls back to asking the user for explicit message text.
        """
        try:
            results = await asyncio.gather(
                call_mcp_tool("jira_get_sprint_board", {"project": project}),
                call_mcp_tool("github_list_open_prs", {}),
                return_exceptions=True,
            )
        except Exception:
            logger.exception("NotifyAgent: sprint data fetch failed")
            return ""

        board = results[0] if isinstance(results[0], dict) else {}
        prs   = results[1] if isinstance(results[1], list) else []
        if not board:
            logger.warning("NotifyAgent: sprint board empty — cannot auto-compose status")
            return ""
        return _format_sprint_status(board, prs)

    @traceable(name="notify_agent", run_type="chain")
    async def run(self, state: SDLCState) -> AgentPayload:
        query     = state["query"]
        user_name = state.get("user_id", "someone")
        project   = state.get("project_id", _settings.DEFAULT_PROJECT)

        logger.info("NotifyAgent.run: query='%s...'", query[:60])

        # ── Agentic extraction: the LLM decides channel + message + intent ─────
        extracted = await self._extract_request(query)

        # An explicit "#channel" token in the raw query is unambiguous and always
        # wins over the LLM's mapping; otherwise use the extracted channel.
        hash_m  = re.search(r'#([\w-]+)', query)
        channel = hash_m.group(1) if hash_m else (extracted.get("channel") or self._default_channel())

        intent       = extracted.get("intent", "unclear")
        message_text = (extracted.get("message") or "").strip()

        # Graceful degradation: if the LLM was unavailable but the user used the
        # conventional "...: <message>" form, still honour the explicit body.
        if not message_text and not extracted and ":" in query:
            message_text = query.split(":", 1)[1].strip()

        # Auto-compose a sprint status when that's the intent and no body was given.
        if not message_text and intent == "sprint_status":
            message_text = await self._compose_sprint_status(project)

        if not message_text:
            clarification = (
                "Please include the message to send. For example:\n\n"
                "- `notify the managers: sprint risk is HIGH`\n"
                "- `notify #backend: PR-5 needs review`\n"
                "- `send sprint status to #engineering-manager`"
            )
            return AgentPayload(
                agent_name="notify",
                confidence=0.0,
                summary="No message content provided",
                structured={"final_response": clarification, "skip_persona": True},
                sources=[],
                hitl_required=False,
                hitl_proposal={},
                response=clarification,
            )

        # ── Show HITL preview card — user must approve before message is sent ─
        preview = (
            f"📢 **Slack Notification Preview**\n\n"
            f"**Channel:** `#{channel}`\n"
            f"**Message:**\n> {message_text}\n\n"
            f"Click **Approve** to send this message, or **Reject** to cancel."
        )

        proposal = {
            "action":   "send_slack",
            "channel":  channel,
            "message":  message_text,
            "sent_by":  user_name,
        }

        return AgentPayload(
            agent_name="notify",
            confidence=1.0,
            summary=f"Slack notification preview → #{channel}",
            structured={"final_response": preview, "skip_persona": True},
            sources=["slack"],
            hitl_required=True,
            hitl_proposal=proposal,
            response=preview,
        )
