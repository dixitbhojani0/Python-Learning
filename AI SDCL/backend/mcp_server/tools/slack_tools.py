"""
backend/mcp_server/tools/slack_tools.py

Slack READ tools over MCP, delegating to the existing SlackConnector (mock by
default — `use_mock: true` in mcp_registry.yaml — since there's no live
workspace; transparent to the LLM). Sending messages is a WRITE action added in
Step 4 behind HITL.
"""
import logging
from typing import Any

from backend.core.config_loader import config as _config

logger = logging.getLogger(__name__)


def _default_channel() -> str:
    """Default Slack channel = agents.yaml > notify_agent.slack_channel (single
    source of truth). Strip leading '#'. Falls back to 'general' only if missing.
    """
    configured = (_config.get_agent("notify_agent") or {}).get("slack_channel", "")
    return configured.lstrip("#") or "general"


def register(mcp: Any, registry: Any) -> None:
    """Add Slack read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def slack_search_messages(query: str, channel: str = "") -> list[dict]:
        """Search Slack messages by keyword within a channel.

        Use to find what the team discussed — incidents, issues raised in chat,
        "was X mentioned in Slack", or a problem that may not have a ticket yet.

        Args:
            query: keywords to search for.
            channel: channel name (e.g. "engineering-manager"); empty = configured
                     default channel from agents.yaml.

        Returns: list of messages, each with user, text, channel, timestamp.
        """
        ch = channel.lstrip("#") or _default_channel()
        logger.info("tool slack_search_messages(query=%r, channel=%r)", query, ch)
        return await registry.get("slack").search_messages(query, ch)

    @mcp.tool()
    async def slack_get_channel_history(channel: str = "") -> list[dict]:
        """Return recent messages from a Slack channel (most recent first).

        Use for "what's the latest in #<channel>", recent team activity/context.

        Args:
            channel: channel name; empty = configured default channel from
                     agents.yaml (notify_agent.slack_channel).

        Returns: list of recent messages (user, text, channel, timestamp).
        """
        ch = channel.lstrip("#") or _default_channel()
        logger.info("tool slack_get_channel_history(channel=%r)", ch)
        return await registry.get("slack").get_channel_history(ch)

    logger.info("slack_tools: registered 2 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """
    Add Slack WRITE tools (sends a message). Excluded from the autonomous gather
    loop; run only via the approved HITL execution path.
    """

    @mcp.tool()
    async def slack_send_message(channel: str, message: str) -> dict:
        """Send a message to a Slack channel. WRITE — requires HITL approval.

        Use for stakeholder notifications (e.g. release GO, ticket created).

        Args:
            channel: channel name (e.g. "engineering-manager" or "#engineering-manager").
            message: the message text.

        Returns: {"ok": bool, ...}.
        """
        logger.info("tool slack_send_message(channel=%r)", channel)
        sent = await registry.get("slack").send_message(channel, message)
        return {"ok": bool(sent), "channel": channel}

    logger.info("slack_tools: registered 1 write tool")
