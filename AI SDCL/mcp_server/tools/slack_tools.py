"""
backend/mcp_server/tools/slack_tools.py

Slack READ tools over MCP, delegating to the existing SlackConnector (mock by
default — `use_mock: true` in mcp_registry.yaml — since there's no live
workspace; transparent to the LLM). Sending messages is a WRITE action added in
Step 4 behind HITL.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add Slack read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def slack_search_messages(query: str, channel: str = "") -> list[dict]:
        """Search Slack messages by keyword across team channels.

        Use to find what the team discussed — incidents, issues raised in chat,
        "was X mentioned in Slack", or a problem that may not have a ticket yet.

        Args:
            query: keywords to search for.
            channel: restrict to one channel (e.g. "backend"); empty = all.

        Returns: list of messages, each with user, text, channel, timestamp.
        """
        logger.info("tool slack_search_messages(query=%r, channel=%r)", query, channel)
        return await registry.get("slack").search_messages(query, channel or "backend")

    @mcp.tool()
    async def slack_get_channel_history(channel: str = "backend") -> list[dict]:
        """Return recent messages from a Slack channel (most recent first).

        Use for "what's the latest in #backend", recent team activity/context.

        Args:
            channel: channel name (default "backend").

        Returns: list of recent messages (user, text, channel, timestamp).
        """
        logger.info("tool slack_get_channel_history(channel=%r)", channel)
        return await registry.get("slack").get_channel_history(channel)

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
