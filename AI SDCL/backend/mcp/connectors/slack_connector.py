"""
backend/mcp/connectors/slack_connector.py

Real Slack connector — Slack Web API via httpx.

Auth: Bot token (xoxb-...) with scopes: channels:history, channels:read, search:read.
Auto-selected by MCPRegistry when SLACK_BOT_TOKEN != "placeholder" and SLACK_USE_MOCK=false.

Slack Web API docs: https://api.slack.com/methods
"""
import logging

import httpx

from backend.core.settings import settings
from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_TIMEOUT  = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _normalize_message(msg: dict, channel_name: str = "") -> dict:
    """Map a Slack API message object to the flat dict format agents expect."""
    return {
        "user":      msg.get("username") or msg.get("user", "unknown"),
        "message":   msg.get("text", ""),
        "timestamp": msg.get("ts", ""),
        "channel":   channel_name,
    }


class SlackConnector(BaseMCPConnector):
    """
    Real Slack connector — calls Slack Web API.

    Requires:
        SLACK_BOT_TOKEN — xoxb-... token from api.slack.com/apps
        Scopes: channels:history, channels:read, search:read
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._headers = {
            "Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json; charset=utf-8",
        }
        # Cache channel name → ID mapping (populated on first channel:history call)
        self._channel_id_cache: dict[str, str] = {}

    def is_available(self) -> bool:
        return bool(
            settings.SLACK_BOT_TOKEN
            and settings.SLACK_BOT_TOKEN not in ("placeholder", "xoxb_placeholder_replace_with_your_token")
            and not settings.SLACK_USE_MOCK
        )

    async def _resolve_channel_id(self, client: httpx.AsyncClient, channel_name: str) -> str | None:
        """
        Convert a channel name (e.g. 'backend') to its Slack channel ID.
        Uses a local cache to avoid repeated API calls within one request.
        """
        name = channel_name.lstrip("#")
        if name in self._channel_id_cache:
            return self._channel_id_cache[name]
        try:
            r = await client.get(
                f"{_API_BASE}/conversations.list",
                params={"exclude_archived": True, "types": "public_channel,private_channel", "limit": 200},
                timeout=_TIMEOUT,
            )
            data = r.json()
            if not data.get("ok"):
                logger.warning("SlackConnector: conversations.list failed — %s", data.get("error"))
                return None
            for ch in data.get("channels", []):
                self._channel_id_cache[ch["name"]] = ch["id"]
            return self._channel_id_cache.get(name)
        except Exception:
            logger.exception("SlackConnector._resolve_channel_id failed for '%s'", channel_name)
            return None

    async def search_messages(
        self,
        query: str,
        channel: str = "backend",
        limit: int = 5,
    ) -> list[dict]:
        """
        Search Slack messages by keyword using search.messages.
        Optionally scopes to a specific channel by prefixing query with 'in:#channel'.
        Requires search:read scope.
        """
        scoped_query = f"in:#{channel} {query}" if channel else query
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{_API_BASE}/search.messages",
                    params={"query": scoped_query, "count": limit, "sort": "timestamp"},
                )
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    logger.warning("SlackConnector.search_messages: API error — %s", data.get("error"))
                    return []
                matches = data.get("messages", {}).get("matches", [])
                results = [
                    {
                        "user":      (m.get("username") or m.get("user", "unknown")),
                        "message":   m.get("text", ""),
                        "timestamp": m.get("ts", ""),
                        "channel":   channel,
                        "permalink": m.get("permalink", ""),
                    }
                    for m in matches
                ]
            logger.info(
                "SlackConnector.search_messages: '%s' in #%s → %d messages",
                query[:50], channel, len(results),
            )
            return results
        except Exception:
            logger.exception("SlackConnector.search_messages failed for query='%s'", query[:50])
            return []

    async def get_channel_history(
        self,
        channel: str = "backend",
        limit: int = 10,
    ) -> list[dict]:
        """
        Fetch the most recent messages from a Slack channel.
        Requires channels:history scope.
        """
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                channel_id = await self._resolve_channel_id(client, channel)
                if not channel_id:
                    logger.warning("SlackConnector: channel '#%s' not found", channel)
                    return []

                r = await client.get(
                    f"{_API_BASE}/conversations.history",
                    params={"channel": channel_id, "limit": limit},
                    timeout=_TIMEOUT,
                )
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    logger.warning("SlackConnector.get_channel_history: API error — %s", data.get("error"))
                    return []

                messages = data.get("messages", [])
                results  = [_normalize_message(m, channel) for m in messages]

            logger.info(
                "SlackConnector.get_channel_history: #%s → %d messages",
                channel, len(results),
            )
            return results
        except Exception:
            logger.exception("SlackConnector.get_channel_history failed for channel='%s'", channel)
            return []

    async def send_message(self, channel: str, message: str) -> bool:
        """
        Post a message to a Slack channel.
        Used by HITL release approval and the scheduler for sprint risk notifications.
        Requires chat:write scope.
        """
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                channel_id = await self._resolve_channel_id(client, channel)
                target = channel_id or channel  # fall back to name if ID resolution fails

                r = await client.post(
                    f"{_API_BASE}/chat.postMessage",
                    json={"channel": target, "text": message, "mrkdwn": True},
                    timeout=_TIMEOUT,
                )
                data = r.json()
                ok = data.get("ok", False)
                if not ok:
                    logger.warning("SlackConnector.send_message: failed — %s", data.get("error"))
                return ok
        except Exception:
            logger.exception("SlackConnector.send_message failed for channel='%s'", channel)
            return False

    # Alias for backward compatibility with scheduler code that calls post_message
    post_message = send_message


# Self-registration — tells MCPRegistry which classes handle "slack" connectors.
# Import this file (via backend/mcp/connectors/__init__.py) to activate.
from backend.mcp.registry import MCPRegistry  # noqa: E402
from backend.mcp.connectors.mock_slack import MockSlackConnector  # noqa: E402
MCPRegistry.register("slack", SlackConnector, MockSlackConnector)
