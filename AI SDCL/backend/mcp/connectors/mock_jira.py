"""
backend/mcp/connectors/mock_jira.py

Mock Jira connector — returns hardcoded ticket data that matches the sprint docs
ingested into Qdrant (sprint_12_plan.md, sprint_health.md).

Phase 7b (TicketAgent) will add create_ticket() with HITL gate.
A real JiraConnector will replace this in a future phase when JIRA_TOKEN is set.
"""
import logging
from backend.core.settings import settings as _settings
from backend.mcp.base_connector import BaseMCPConnector

_DEFAULT_PROJECT = _settings.DEFAULT_PROJECT

logger = logging.getLogger(__name__)

# ── Mock ticket data — mirrors SDLC project SDLC Sprint 12 ─────────────────
# These ticket IDs are referenced in sprint_docs and Slack messages, so the
# LLM can correlate live Jira data with historical RAG context.

_MOCK_TICKETS = [
    {
        "id":          "SDLC-1031",
        "title":       "Payment gateway integration — vendor SSL certificate renewal",
        "status":      "BLOCKED",
        "priority":    "HIGH",
        "assignee":    "bob",
        "sprint":      "Sprint 12",
        "created":     "2026-05-20",
        "updated":     "2026-05-28",
        "description": "Stripe sandbox unreachable. Vendor SSL certificate renewal has been pending for 11 days. No ETA from vendor.",
        "labels":      ["payment", "integration", "blocked", "vendor", "ssl"],
        "blockers":    ["Vendor SSL certificate renewal — no ETA from vendor"],
    },
    {
        "id":          "SDLC-1038",
        "title":       "Dashboard API feature — /api/v2/users endpoint integration",
        "status":      "IN_PROGRESS",
        "priority":    "HIGH",
        "assignee":    "alice",
        "sprint":      "Sprint 12",
        "created":     "2026-05-22",
        "updated":     "2026-05-28",
        "description": "Dashboard integration tests for /api/v2/users. Was blocked by DB connection pool exhaustion after nginx config change. Pool size increased to 50 — issue resolved.",
        "labels":      ["dashboard", "api", "nginx", "integration-tests"],
        "blockers":    [],
    },
    {
        "id":          "SDLC-1042",
        "title":       "CORS error on /api/v2/auth — permanent nginx fix needed",
        "status":      "OPEN",
        "priority":    "MEDIUM",
        "assignee":    "unassigned",
        "sprint":      "Sprint 12",
        "created":     "2026-05-28",
        "updated":     "2026-05-28",
        "description": "CORS headers (Access-Control-Allow-Origin) disappear after nginx -s reload. Temporary workaround: reload again. Permanent fix: update nginx.conf to persist headers across worker restarts.",
        "labels":      ["cors", "nginx", "auth", "headers", "infrastructure"],
        "blockers":    [],
    },
    {
        "id":          "SDLC-1025",
        "title":       "Auth service refactor — JWT token validation",
        "status":      "DONE",
        "priority":    "MEDIUM",
        "assignee":    "charlie",
        "sprint":      "Sprint 11",
        "created":     "2026-05-01",
        "updated":     "2026-05-15",
        "description": "Refactored auth service to validate JWT tokens. All unit and integration tests passing.",
        "labels":      ["auth", "jwt", "refactor", "done"],
        "blockers":    [],
    },
]


class MockJiraConnector(BaseMCPConnector):
    """
    Returns fake Jira ticket data for local demo.

    search_tickets()      — keyword search over title + description + labels
    get_blocked_tickets() — returns all tickets with status == BLOCKED
    get_sprint_board()    — returns sprint summary statistics
    """

    def is_available(self) -> bool:
        return True   # always available — reads from memory, no network call

    async def search_tickets(self, query: str, project: str = _DEFAULT_PROJECT) -> list[dict]:
        """Return tickets whose title, description, or labels contain any query word."""
        query_words = query.lower().split()
        results = []
        for ticket in _MOCK_TICKETS:
            searchable = " ".join([
                ticket["title"],
                ticket["description"],
                " ".join(ticket["labels"]),
                ticket["status"],
            ]).lower()
            if any(word in searchable for word in query_words):
                results.append(ticket)

        logger.debug("MockJira.search_tickets: query='%s' → %d tickets", query[:50], len(results))
        return results

    async def get_blocked_tickets(self, project: str = _DEFAULT_PROJECT) -> list[dict]:
        """Return all tickets currently in BLOCKED status."""
        blocked = [t for t in _MOCK_TICKETS if t["status"] == "BLOCKED"]
        logger.debug("MockJira.get_blocked_tickets: %d blocked tickets", len(blocked))
        return blocked

    async def get_sprint_board(self, project: str = _DEFAULT_PROJECT) -> dict:
        """Return current sprint summary statistics."""
        return {
            "sprint":            "Sprint 12",
            "project":           project,
            "goal":              "Complete Dashboard API feature and unblock Payment Gateway integration",
            "total_tickets":     8,
            "done":              2,
            "in_progress":       3,
            "blocked":           2,
            "not_started":       1,
            "completion_pct":    25,
            "days_remaining":    2,
            "risk_level":        "HIGH",
            "blocked_tickets":   ["SDLC-1031", "SDLC-1042"],
        }

    async def get_project_members(self, project: str = _DEFAULT_PROJECT) -> list[dict]:
        """Return team members who can be assigned to tickets in this project."""
        logger.debug("MockJira.get_project_members: project='%s'", project)
        return [
            {"name": "alice",   "display_name": "Alice",   "email": "alice@company.com",   "active": True, "account_id": "mock-acct-alice"},
            {"name": "bob",     "display_name": "Bob",     "email": "bob@company.com",     "active": True, "account_id": "mock-acct-bob"},
            {"name": "charlie", "display_name": "Charlie", "email": "charlie@company.com", "active": True, "account_id": "mock-acct-charlie"},
            {"name": "diana",   "display_name": "Diana",   "email": "diana@company.com",   "active": True, "account_id": "mock-acct-diana"},
        ]

    async def get_ticket(self, ticket_id: str) -> dict | None:
        """Return a mock ticket by ID, or None if not found."""
        for t in _MOCK_TICKETS:
            if t["id"].upper() == ticket_id.upper():
                logger.debug("MockJira.get_ticket: found '%s'", ticket_id)
                return t
        logger.debug("MockJira.get_ticket: ticket '%s' not found in mock data", ticket_id)
        return None

    async def create_ticket(self, title: str, description: str, priority: str = "MEDIUM", assignee: str = "", labels: list | None = None) -> dict:
        """Mock ticket creation — returns a fake ticket ID."""
        logger.debug("MockJira.create_ticket: title='%s'", title[:60])
        return {"id": "SDLC-9999", "url": "https://mock.atlassian.net/browse/SDLC-9999"}

    async def assign_ticket(self, ticket_id: str, account_id: str) -> dict:
        """Mock ticket assignment — always succeeds."""
        logger.debug("MockJira.assign_ticket: %s → %s", ticket_id, account_id)
        return {"success": True, "ticket_id": ticket_id, "account_id": account_id}

    async def add_comment(self, ticket_id: str, body: str) -> dict:
        """Mock comment add — always succeeds."""
        logger.debug("MockJira.add_comment: %s ← %s", ticket_id, body[:60])
        return {"success": True, "ticket_id": ticket_id}
