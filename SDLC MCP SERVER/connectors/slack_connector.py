"""
connectors/slack_connector.py

Real Slack connector — Slack Web API via httpx.
Auth: Bot token (xoxb-...) with scopes: channels:history, channels:read, chat:write.
"""
import logging

from core.settings import settings
from connectors.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"


def _normalize_message(msg: dict, channel_name: str = "") -> dict:
    return {
        "user":      msg.get("username") or msg.get("user", "unknown"),
        "message":   msg.get("text", ""),
        "timestamp": msg.get("ts", ""),
        "channel":   channel_name,
    }


class SlackConnector(BaseMCPConnector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._headers = {
            "Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json; charset=utf-8",
        }
        self._channel_id_cache: dict[str, str] = {}

    def is_available(self) -> bool:
        return bool(
            settings.SLACK_BOT_TOKEN
            and settings.SLACK_BOT_TOKEN not in ("placeholder", "xoxb_placeholder_replace_with_your_token")
            and not settings.SLACK_USE_MOCK
        )

    async def _populate_cache(self, endpoint: str) -> None:
        cursor = ""
        for _ in range(5):
            # public_channel only — matches the token's granted scopes (channels:read/history).
            # Slack requires the scope for EVERY type requested, so asking for private_channel
            # too (needs groups:read, not granted) was failing the WHOLE list call with
            # missing_scope — even for public channels that channels:read alone can resolve.
            params = {"exclude_archived": True, "types": "public_channel", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                r = await self.http.get(f"{_API_BASE}/{endpoint}", params=params)
            except Exception:
                logger.exception("SlackConnector._populate_cache: %s call failed", endpoint)
                return
            data = r.json() if r.is_success else {"ok": False, "error": f"http {r.status_code}"}
            if not data.get("ok"):
                logger.warning("SlackConnector: %s failed — %s", endpoint, data.get("error"))
                return
            for ch in data.get("channels", []):
                self._channel_id_cache[ch["name"]] = ch["id"]
            cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                return

    async def _resolve_channel_id(self, channel_name: str) -> str | None:
        name = channel_name.lstrip("#")
        if name in self._channel_id_cache:
            return self._channel_id_cache[name]
        await self._populate_cache("conversations.list")
        if name in self._channel_id_cache:
            return self._channel_id_cache[name]
        await self._populate_cache("users.conversations")
        return self._channel_id_cache.get(name)

    async def _fetch_history(self, channel_id: str, channel_name: str, limit: int = 200) -> list[dict]:
        async def _hit() -> dict:
            r = await self.http.get(f"{_API_BASE}/conversations.history", params={"channel": channel_id, "limit": limit})
            return r.json() if r.is_success else {"ok": False, "error": f"http {r.status_code}"}

        data = await _hit()
        if data.get("ok"):
            return data.get("messages", [])

        err = data.get("error", "unknown")
        if err == "not_in_channel":
            join_r = await self.http.post(f"{_API_BASE}/conversations.join", json={"channel": channel_id})
            join_data = join_r.json() if join_r.is_success else {"ok": False}
            if not join_data.get("ok"):
                clean = channel_name.lstrip("#")
                logger.warning("SlackConnector: bot not in #%s and join failed", clean)
                return [{"error": f"bot not in #{clean} (private channel — invite the bot manually)", "channel": clean}]
            logger.info("SlackConnector: auto-joined #%s", channel_name.lstrip("#"))
            data = await _hit()
            if data.get("ok"):
                return data.get("messages", [])
            err = data.get("error", "unknown")

        logger.warning("SlackConnector: conversations.history error for #%s — %s", channel_name, err)
        return [{"error": f"slack api error: {err}", "channel": channel_name.lstrip("#")}]

    async def search_messages(self, query: str, channel: str = "backend", limit: int = 5) -> list[dict]:
        try:
            channel_id = await self._resolve_channel_id(channel)
            if not channel_id:
                return [{"error": f"channel '#{channel.lstrip('#')}' not found", "channel": channel.lstrip("#")}]
            history = await self._fetch_history(channel_id, channel, limit=200)
            if history and history[0].get("error"):
                return history
            q = (query or "").strip().lower()
            matches = [m for m in history if q in (m.get("text") or "").lower()] if q else history
            results = [_normalize_message(m, channel) for m in matches[:limit]]
            logger.info("SlackConnector.search_messages: '%s' in #%s → %d matched", query[:50], channel, len(results))
            return results
        except Exception:
            logger.exception("SlackConnector.search_messages failed for query='%s'", query[:50])
            return []

    async def get_channel_history(self, channel: str = "backend", limit: int = 10) -> list[dict]:
        try:
            channel_id = await self._resolve_channel_id(channel)
            if not channel_id:
                return [{"error": f"channel '#{channel.lstrip('#')}' not found", "channel": channel.lstrip("#")}]
            history = await self._fetch_history(channel_id, channel, limit=limit)
            if history and history[0].get("error"):
                return history
            results = [_normalize_message(m, channel) for m in history]
            logger.info("SlackConnector.get_channel_history: #%s → %d messages", channel, len(results))
            return results
        except Exception:
            logger.exception("SlackConnector.get_channel_history failed for channel='%s'", channel)
            return []

    async def send_message(self, channel: str, message: str) -> dict:
        """Returns {"success": bool, "error": str|None} — same shape as other write ops."""
        try:
            channel_id = await self._resolve_channel_id(channel)
            target = channel_id or channel
            r = await self.http.post(
                f"{_API_BASE}/chat.postMessage",
                json={"channel": target, "text": message, "mrkdwn": True},
            )
            data = r.json() if r.is_success else {"ok": False, "error": f"http {r.status_code}"}
            if not data.get("ok"):
                logger.warning("SlackConnector.send_message: failed — %s", data.get("error"))
                return {"success": False, "error": str(data.get("error", "unknown"))}
            return {"success": True, "error": None}
        except Exception:
            logger.exception("SlackConnector.send_message failed for channel='%s'", channel)
            return {"success": False, "error": "request failed"}

    post_message = send_message


from registry import MCPRegistry  # noqa: E402
MCPRegistry.register("slack", SlackConnector)
