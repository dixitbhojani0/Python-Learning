"""
backend/mcp/connectors/mock_teams.py

Mock Microsoft Teams connector — reads from data/mock_teams/*.json.

Mirrors the MockSlackConnector interface exactly so the cross-source agent can
call Slack and Teams with the same method names. The two sources hold
complementary content: Slack carries the frontend-reported symptom, Teams carries
the backend root-cause discussion. Correlating both is the "killer demo" the
solution document describes (Section 26, Scenario 1).

search_messages()      — keyword search over message content
get_channel_history()  — return last N messages (simulates live channel feed)
list_members()         — static team member list for the demo
"""
import json
import logging
from pathlib import Path

from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

# Path relative to repo root — resolved at import time
_MOCK_DATA_DIR = Path(__file__).parents[3] / "data" / "mock_teams"


def _load_channel(filename: str) -> list[dict]:
    """Load messages from a mock Teams JSON file. Returns [] on any error."""
    path = _MOCK_DATA_DIR / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("MockTeams: could not load %s — %s", path, exc)
        return []


# Channel registry — add more channels here as data/mock_teams/*.json files are added
_CHANNELS: dict[str, str] = {
    "backend":  "backend_channel.json",
    "general":  "backend_channel.json",   # fallback to backend for demo
}

# Common words that must NOT count as a match (see mock_slack for rationale).
_STOPWORDS = {
    "the", "is", "a", "an", "of", "to", "in", "on", "for", "and", "or", "what",
    "whats", "show", "me", "please", "can", "you", "tell", "about", "how", "do",
    "does", "this", "that", "with", "from", "are", "was", "were", "i", "we", "my",
    "our", "it", "be", "as", "at", "by", "any", "all", "give", "get", "there",
}


def _meaningful_keywords(query: str) -> list[str]:
    """Query words worth matching on — drops stopwords and 1-2 char tokens."""
    return [w for w in query.lower().split() if len(w) > 2 and w not in _STOPWORDS]


class MockTeamsConnector(BaseMCPConnector):
    """
    Returns Microsoft Teams messages from local JSON files.

    search_messages()      — keyword search (any query word in message text)
    get_channel_history()  — chronological message list, newest first
    """

    def is_available(self) -> bool:
        return _MOCK_DATA_DIR.exists()

    async def search_messages(
        self,
        query:   str,
        channel: str = "backend",
        limit:   int = 5,
    ) -> list[dict]:
        """Return messages that contain any meaningful query keyword (stopwords ignored)."""
        filename = _CHANNELS.get(channel, "backend_channel.json")
        messages = _load_channel(filename)
        keywords = _meaningful_keywords(query)
        if not keywords:
            return []   # nothing substantive to match → no false positives

        results = [
            m for m in messages
            if any(kw in m["message"].lower() for kw in keywords)
        ]

        logger.debug(
            "MockTeams.search_messages: query='%s' channel='%s' → %d matches",
            query[:50], channel, len(results),
        )
        return results[:limit]

    async def get_channel_history(
        self,
        channel: str = "backend",
        limit:   int = 10,
    ) -> list[dict]:
        """Return the most recent messages from a channel, newest first."""
        filename    = _CHANNELS.get(channel, "backend_channel.json")
        messages    = _load_channel(filename)
        sorted_msgs = sorted(messages, key=lambda m: m.get("timestamp", ""), reverse=True)
        logger.debug(
            "MockTeams.get_channel_history: channel='%s' → %d messages",
            channel, min(limit, len(sorted_msgs)),
        )
        return sorted_msgs[:limit]

    async def list_members(self, channel: str = "backend") -> list[dict]:
        """Static member list for the demo — mirrors get_project_members shape."""
        return [
            {"name": "ravi",  "display_name": "Ravi",  "email": "ravi@company.com"},
            {"name": "meera", "display_name": "Meera", "email": "meera@company.com"},
        ]

    async def send_message(self, channel: str, message: str) -> bool:
        """Log the outgoing message instead of calling the real Teams API."""
        logger.info("MockTeams.send_message: channel='%s' message='%s...'", channel, message[:80])
        return True

    # Aliases so callers can use either name (matches Slack connector convention)
    post_message = send_message
