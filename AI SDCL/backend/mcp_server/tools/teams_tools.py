"""
backend/mcp_server/tools/teams_tools.py

Microsoft Teams READ tools over MCP, delegating to the existing TeamsConnector
(mock by default — `use_mock: true` — unless an Azure AD app is configured).
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add Teams read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def teams_search_messages(query: str, channel: str = "backend") -> list[dict]:
        """Search Microsoft Teams channel messages by keyword.

        Use like slack_search_messages but for the Teams workspace — finding what
        was discussed there.

        Args:
            query: keywords to search for.
            channel: channel name (default "backend").

        Returns: list of messages (user, text, channel, timestamp).
        """
        logger.info("tool teams_search_messages(query=%r, channel=%r)", query, channel)
        return await registry.get("teams").search_messages(query, channel)

    @mcp.tool()
    async def teams_get_channel_history(channel: str = "backend") -> list[dict]:
        """Return recent messages from a Microsoft Teams channel (most recent first).

        Args:
            channel: channel name (default "backend").

        Returns: list of recent messages (user, text, channel, timestamp).
        """
        logger.info("tool teams_get_channel_history(channel=%r)", channel)
        return await registry.get("teams").get_channel_history(channel)

    logger.info("teams_tools: registered 2 read tools")
