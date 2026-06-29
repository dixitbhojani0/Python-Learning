"""
backend/mcp/connectors/teams_connector.py

Real Microsoft Teams connector — Microsoft Graph API via httpx.

Auth: Azure AD app registration (client-credentials flow). Set in .env:
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
Graph permissions: ChannelMessage.Read.All, ChannelMessage.Send, Team.ReadBasic.All

Auto-selected by MCPRegistry when those credentials are present AND the connector
config sets use_mock: false. Otherwise the MockTeamsConnector is used — so the
demo runs with zero Azure setup. Credentials are read defensively via getattr so
the absence of Azure settings can never break startup.

Graph API docs: https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview
"""
import logging

import httpx

from backend.core.settings import settings
from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TIMEOUT    = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _cred(name: str) -> str:
    """Read an Azure credential from settings without requiring it to be defined."""
    return getattr(settings, name, "") or ""


def _normalize_message(msg: dict, channel_name: str = "") -> dict:
    """Map a Graph chatMessage object to the flat dict the agents expect."""
    body   = (msg.get("body") or {}).get("content", "")
    sender = (((msg.get("from") or {}).get("user")) or {}).get("displayName", "unknown")
    return {
        "user":      sender,
        "message":   body,
        "timestamp": msg.get("createdDateTime", ""),
        "channel":   channel_name,
    }


class TeamsConnector(BaseMCPConnector):
    """
    Real Microsoft Teams connector — calls Microsoft Graph.

    Method signatures match MockTeamsConnector / SlackConnector so the
    cross-source and notify agents are connector-agnostic.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token: str | None = None

    def is_available(self) -> bool:
        # use_mock comes from config/mcp_registry.yaml (config-driven, like Slack).
        if self.config.get("use_mock", True):
            return False
        return bool(_cred("AZURE_TENANT_ID") and _cred("AZURE_CLIENT_ID") and _cred("AZURE_CLIENT_SECRET"))

    async def _get_token(self, client: httpx.AsyncClient) -> str | None:
        """Acquire an app-only Graph token via the client-credentials flow (cached)."""
        if self._token:
            return self._token
        try:
            r = await client.post(
                f"https://login.microsoftonline.com/{_cred('AZURE_TENANT_ID')}/oauth2/v2.0/token",
                data={
                    "client_id":     _cred("AZURE_CLIENT_ID"),
                    "client_secret": _cred("AZURE_CLIENT_SECRET"),
                    "scope":         "https://graph.microsoft.com/.default",
                    "grant_type":    "client_credentials",
                },
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            self._token = r.json().get("access_token")
            return self._token
        except Exception:
            logger.exception("TeamsConnector: token acquisition failed")
            return None

    async def _fetch_channel_messages(self, channel: str, limit: int) -> list[dict]:
        """
        Fetch recent messages for a channel. Channel/team IDs are read from the
        connector config (team_id, channels map) so no name->ID lookup is needed.
        Returns [] on any failure — the agent degrades to Slack + RAG.
        """
        team_id  = self.config.get("team_id", "")
        channels = self.config.get("channels", {})
        chan_id  = channels.get(channel) or channels.get("default", "")
        if not team_id or not chan_id:
            logger.warning("TeamsConnector: team_id/channel id not configured for '%s'", channel)
            return []

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                token = await self._get_token(client)
                if not token:
                    return []
                r = await client.get(
                    f"{_GRAPH_BASE}/teams/{team_id}/channels/{chan_id}/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"$top": limit},
                )
                r.raise_for_status()
                msgs = r.json().get("value", [])
                return [_normalize_message(m, channel) for m in msgs]
        except Exception:
            logger.exception("TeamsConnector: fetch messages failed for channel='%s'", channel)
            return []

    async def get_channel_history(self, channel: str = "backend", limit: int = 10) -> list[dict]:
        results = await self._fetch_channel_messages(channel, limit)
        logger.info("TeamsConnector.get_channel_history: #%s → %d messages", channel, len(results))
        return results

    async def search_messages(self, query: str, channel: str = "backend", limit: int = 5) -> list[dict]:
        """Graph has no simple per-channel text search; fetch recent and filter locally."""
        messages = await self._fetch_channel_messages(channel, max(limit * 4, 20))
        keywords = query.lower().split()
        results  = [m for m in messages if any(kw in m["message"].lower() for kw in keywords)]
        return results[:limit]

    async def send_message(self, channel: str, message: str) -> bool:
        team_id  = self.config.get("team_id", "")
        channels = self.config.get("channels", {})
        chan_id  = channels.get(channel) or channels.get("default", "")
        if not team_id or not chan_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                token = await self._get_token(client)
                if not token:
                    return False
                r = await client.post(
                    f"{_GRAPH_BASE}/teams/{team_id}/channels/{chan_id}/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"body": {"content": message}},
                )
                return r.is_success
        except Exception:
            logger.exception("TeamsConnector.send_message failed for channel='%s'", channel)
            return False

    post_message = send_message


# Self-registration — tells MCPRegistry which classes handle "teams" connectors.
from backend.mcp.registry import MCPRegistry  # noqa: E402
from backend.mcp.connectors.mock_teams import MockTeamsConnector  # noqa: E402
MCPRegistry.register("teams", TeamsConnector, MockTeamsConnector)
