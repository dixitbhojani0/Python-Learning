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

    async def _populate_cache(self, client: httpx.AsyncClient, endpoint: str) -> None:
        """
        Fill _channel_id_cache from `conversations.list` (workspace-wide) or
        `users.conversations` (bot's own member list). Paginated. Errors logged
        with the Slack error code so the host sees missing-scope problems.
        """
        cursor = ""
        for _ in range(5):  # cap pages: 5 * 200 = 1000 channels, plenty for any workspace
            params = {
                "exclude_archived": True,
                "types": "public_channel,private_channel",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                r = await client.get(f"{_API_BASE}/{endpoint}", params=params, timeout=_TIMEOUT)
            except Exception:
                logger.exception("SlackConnector._populate_cache: %s call failed", endpoint)
                return
            data = r.json() if r.is_success else {"ok": False, "error": f"http {r.status_code}"}
            if not data.get("ok"):
                logger.warning("SlackConnector: %s failed — %s (check bot scopes: channels:read, groups:read)",
                               endpoint, data.get("error"))
                return
            for ch in data.get("channels", []):
                self._channel_id_cache[ch["name"]] = ch["id"]
            cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                return

    async def _resolve_channel_id(self, client: httpx.AsyncClient, channel_name: str) -> str | None:
        """
        Convert a channel name (e.g. 'engineering-manager') to its Slack channel ID.

        Resolution order:
          1. local cache (avoid repeat API calls within one request)
          2. `conversations.list` — workspace-wide view (needs channels:read/groups:read)
          3. `users.conversations` — bot's own member list (fallback for restricted
             scopes or private channels the bot is invited to but `conversations.list`
             doesn't surface)
        """
        name = channel_name.lstrip("#")
        if name in self._channel_id_cache:
            return self._channel_id_cache[name]

        await self._populate_cache(client, "conversations.list")
        if name in self._channel_id_cache:
            return self._channel_id_cache[name]

        # Fallback: lists conversations the bot is a member of by id+name. Often
        # succeeds when the workspace-wide list is scope-restricted, since the bot
        # IS in the channel it's trying to read from.
        await self._populate_cache(client, "users.conversations")
        return self._channel_id_cache.get(name)

    async def _fetch_history(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
        channel_name: str,
        limit: int = 200,
    ) -> list[dict]:
        """
        Fetch conversations.history, auto-joining a public channel once if the bot
        isn't a member. Returns a list of raw Slack message dicts on success, OR a
        single-item list `[{"error": "...", "channel": "..."}]` on real failures so
        the host sees the real reason instead of a silent empty list.
        """
        async def _hit() -> dict:
            r = await client.get(
                f"{_API_BASE}/conversations.history",
                params={"channel": channel_id, "limit": limit},
                timeout=_TIMEOUT,
            )
            return r.json() if r.is_success else {"ok": False, "error": f"http {r.status_code}"}

        data = await _hit()
        if data.get("ok"):
            return data.get("messages", [])

        err = data.get("error", "unknown")
        if err == "not_in_channel":
            # Auto-join only works for PUBLIC channels (scope: channels:join).
            join_r = await client.post(
                f"{_API_BASE}/conversations.join",
                json={"channel": channel_id},
                timeout=_TIMEOUT,
            )
            join_data = join_r.json() if join_r.is_success else {"ok": False, "error": "http"}
            if not join_data.get("ok"):
                clean = channel_name.lstrip("#")
                logger.warning("SlackConnector: bot not in #%s and join failed — %s", clean, join_data.get("error"))
                return [{
                    "error":   f"bot not in #{clean} (private channel — invite the bot manually)",
                    "channel": clean,
                }]
            logger.info("SlackConnector: auto-joined #%s", channel_name.lstrip("#"))
            data = await _hit()
            if data.get("ok"):
                return data.get("messages", [])
            err = data.get("error", "unknown")

        logger.warning("SlackConnector: conversations.history error for #%s — %s", channel_name, err)
        return [{"error": f"slack api error: {err}", "channel": channel_name.lstrip("#")}]

    async def search_messages(
        self,
        query: str,
        channel: str = "backend",
        limit: int = 5,
    ) -> list[dict]:
        """
        Find Slack messages by keyword within a channel.

        We use conversations.history + local substring filter, NOT search.messages —
        the Slack search API only accepts USER tokens; bot tokens (xoxb-) fail
        silently. Standard workaround for bot apps. Requires channels:history (and
        channels:join for public auto-join). Returns at most `limit` matches.
        """
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                channel_id = await self._resolve_channel_id(client, channel)
                if not channel_id:
                    return [{"error": f"channel '#{channel.lstrip('#')}' not found", "channel": channel.lstrip("#")}]
                history = await self._fetch_history(client, channel_id, channel, limit=200)
                if history and history[0].get("error"):
                    return history   # propagate the real reason to the host
                q = (query or "").strip().lower()
                matches = [m for m in history if q in (m.get("text") or "").lower()] if q else history
                results = [_normalize_message(m, channel) for m in matches[:limit]]
            logger.info(
                "SlackConnector.search_messages: '%s' in #%s → %d/%d matched",
                query[:50], channel, len(results), len(matches),
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
        Most recent messages from a Slack channel. Auto-joins public channels if
        the bot isn't a member; returns a clear error item for private channels.
        Requires channels:history (+ channels:join for public auto-join).
        """
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                channel_id = await self._resolve_channel_id(client, channel)
                if not channel_id:
                    return [{"error": f"channel '#{channel.lstrip('#')}' not found", "channel": channel.lstrip("#")}]
                history = await self._fetch_history(client, channel_id, channel, limit=limit)
                if history and history[0].get("error"):
                    return history
                results = [_normalize_message(m, channel) for m in history]
            logger.info("SlackConnector.get_channel_history: #%s → %d messages", channel, len(results))
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
MCPRegistry.register("slack", SlackConnector)
