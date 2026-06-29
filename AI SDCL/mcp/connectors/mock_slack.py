"""
backend/mcp/connectors/mock_slack.py

Mock Slack connector — reads from data/mock_slack/backend_channel.json.

Why read from a file instead of hardcoding?
  The same JSON is ingested into Qdrant by the RAG pipeline. When the LLM sees
  "alice said X" in both the RAG context AND the MCP live data, it can correctly
  attribute the message to a specific time and person. Consistency matters.

search_messages()      — keyword search over message content
get_channel_history()  — return last N messages (simulates live channel feed)
"""
import json
import logging
from pathlib import Path

from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

# Path relative to repo root — resolved at import time
_MOCK_DATA_DIR = Path(__file__).parents[3] / "data" / "mock_slack"


def _load_channel(filename: str) -> list[dict]:
    """Load messages from a mock Slack JSON file. Returns [] on any error."""
    path = _MOCK_DATA_DIR / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("MockSlack: could not load %s — %s", path, exc)
        return []


# Channel registry — add more channels here as data/mock_slack/*.json files are added
_CHANNELS: dict[str, str] = {
    "backend":  "backend_channel.json",
    "general":  "backend_channel.json",   # fallback to backend for demo
}

# Common words that must NOT count as a match. Without this, a query like
# "what is the capital of France?" matches backend messages on "is"/"the",
# which falsely makes the agent think it has relevant Slack data.
_STOPWORDS = {
    "the", "is", "a", "an", "of", "to", "in", "on", "for", "and", "or", "what",
    "whats", "show", "me", "please", "can", "you", "tell", "about", "how", "do",
    "does", "this", "that", "with", "from", "are", "was", "were", "i", "we", "my",
    "our", "it", "be", "as", "at", "by", "any", "all", "give", "get", "there",
}


def _meaningful_keywords(query: str) -> list[str]:
    """Query words worth matching on — drops stopwords and 1-2 char tokens."""
    return [w for w in query.lower().split() if len(w) > 2 and w not in _STOPWORDS]


class MockSlackConnector(BaseMCPConnector):
    """
    Returns Slack messages from local JSON files.

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
        filename  = _CHANNELS.get(channel, "backend_channel.json")
        messages  = _load_channel(filename)
        keywords  = _meaningful_keywords(query)
        if not keywords:
            return []   # nothing substantive to match → no false positives

        results = [
            m for m in messages
            if any(kw in m["message"].lower() for kw in keywords)
        ]

        logger.debug(
            "MockSlack.search_messages: query='%s' channel='%s' → %d matches",
            query[:50], channel, len(results),
        )
        return results[:limit]

    async def get_channel_history(
        self,
        channel: str = "backend",
        limit:   int = 10,
    ) -> list[dict]:
        """Return the most recent messages from a channel."""
        filename = _CHANNELS.get(channel, "backend_channel.json")
        messages = _load_channel(filename)
        # Sort newest first, then cap
        sorted_msgs = sorted(messages, key=lambda m: m.get("timestamp", ""), reverse=True)
        logger.debug(
            "MockSlack.get_channel_history: channel='%s' → %d messages",
            channel, min(limit, len(sorted_msgs)),
        )
        return sorted_msgs[:limit]

    async def send_message(self, channel: str, message: str) -> bool:
        """Log the outgoing message instead of calling real Slack API."""
        logger.info("MockSlack.send_message: channel='%s' message='%s...'", channel, message[:80])
        return True

    post_message = send_message
