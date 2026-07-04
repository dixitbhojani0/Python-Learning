"""
tools/slack_tools.py

Slack READ and WRITE tools exposed over MCP.
"""
import logging
from typing import Any

from core.config_loader import config as _config

logger = logging.getLogger(__name__)


def _default_channel() -> str:
    """Default channel from agents.yaml > notify_agent.slack_channel. Falls back to 'general'."""
    # In the standalone server, fall back to 'general' since agents.yaml is not loaded.
    return "general"


def register(mcp: Any, registry: Any) -> None:
    """Add Slack read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def slack_search_messages(query: str, channel: str = "") -> list[dict]:
        """Search Slack messages by keyword within a channel.

        Args:
            query: keywords to search for.
            channel: channel name (e.g. "engineering-manager"); empty = 'general'.

        Returns: list of messages with user, text, channel, timestamp.
        """
        ch = channel.lstrip("#") or _default_channel()
        logger.info("tool slack_search_messages(query=%r, channel=%r)", query, ch)
        return await registry.get("slack").search_messages(query, ch)

    @mcp.tool()
    async def slack_get_channel_history(channel: str = "") -> list[dict]:
        """Return recent messages from a Slack channel (most recent first).

        Args:
            channel: channel name; empty = 'general'.

        Returns: list of recent messages (user, text, channel, timestamp).
        """
        ch = channel.lstrip("#") or _default_channel()
        logger.info("tool slack_get_channel_history(channel=%r)", ch)
        return await registry.get("slack").get_channel_history(ch)

    logger.info("slack_tools: registered 2 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """Add Slack WRITE tools. Run only via HITL approval."""

    @mcp.tool()
    async def slack_send_message(channel: str, message: str) -> dict:
        """Send a message to a Slack channel. WRITE — requires HITL approval.

        Args:
            channel: channel name (e.g. "engineering-manager").
            message: the message text.

        Returns: {"ok": bool, "channel": str}.
        """
        logger.info("tool slack_send_message(channel=%r)", channel)
        sent = await registry.get("slack").send_message(channel, message)
        return {"ok": bool(sent), "channel": channel}

    logger.info("slack_tools: registered 1 write tool")
