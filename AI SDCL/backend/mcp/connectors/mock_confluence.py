"""
backend/mcp/connectors/mock_confluence.py

Mock Confluence connector — returns the 3 SDLC project documentation pages
for offline / dev mode when real Confluence credentials are not configured.

Pages mirror what should be created in real Confluence:
  - INC-002: DB Connection Pool Exhaustion (incident report)
  - ADR-002: Database Connection Pool Configuration (architecture decision)
  - Release Notes v2.1.0 (release history)
"""
import logging

from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_MOCK_PAGES = [
    {
        "id":        "mock-001",
        "title":     "INC-002: DB Connection Pool Exhaustion — May 28, 2026",
        "url":       "https://mock.atlassian.net/wiki/spaces/SDLC/pages/mock-001",
        "space_key": "SDLC",
        "content": (
            "Severity: HIGH. Duration: 2h 30m. Affected: /api/v2/users, auth-service.\n"
            "Reported by: alice. Resolved by: charlie.\n\n"
            "Timeline:\n"
            "10:00 — alice reports 500 errors on /api/v2/users after nginx config update.\n"
            "10:15 — charlie identifies QueuePool exhaustion in auth-service logs.\n"
            "12:00 — charlie increases pool_size to 50 in db.yaml and restarts auth-service.\n"
            "12:15 — alice confirms /api/v2/users returning 200.\n\n"
            "Root Cause: DB connection pool was set to 20. The nginx config update increased "
            "concurrent requests beyond pool capacity, triggering QueuePool limit.\n\n"
            "Resolution: Increased pool_size from 20 to 50 in config/db.yaml. PR-47 merged.\n"
            "Related: Dashboard API feature, PR-47 (merged)."
        ),
    },
    {
        "id":        "mock-002",
        "title":     "ADR-002: Database Connection Pool Configuration",
        "url":       "https://mock.atlassian.net/wiki/spaces/SDLC/pages/mock-002",
        "space_key": "SDLC",
        "content": (
            "Date: 2026-05-28. Status: ACTIVE. Author: charlie.\n\n"
            "Decision: Set DB pool_size=50 and max_overflow=10 for all services connecting to PostgreSQL.\n\n"
            "Rationale: Pool exhaustion incident INC-002 showed the default pool of 20 connections was "
            "insufficient under nginx-proxied concurrent load. A monitoring alert now triggers at "
            "80% pool utilization via SDLC-1039.\n\n"
            "Related: INC-002, PR-47, SDLC-1038, SDLC-1039."
        ),
    },
    {
        "id":        "mock-003",
        "title":     "Release Notes v2.1.0 — May 15, 2026",
        "url":       "https://mock.atlassian.net/wiki/spaces/SDLC/pages/mock-003",
        "space_key": "SDLC",
        "content": (
            "Version: v2.1.0 (MINOR). Released: 2026-05-15. Released by: charlie.\n\n"
            "New Features:\n"
            "- GET /api/v2/dashboard/summary endpoint added.\n"
            "- User role field added to JWT payload (optional, backward compatible).\n"
            "- Redis pool size now configurable via environment variable.\n\n"
            "Bug Fixes:\n"
            "- DB pool 500 errors fixed (pool_size increased from 20 to 50).\n"
            "- CORS headers fixed on nginx reload (add_header always flag added).\n"
            "- nginx 502 timeout fixed (keepalive increased from 15s to 30s).\n\n"
            "Infrastructure: Redis upgraded 7.2 to 7.4-alpine. Qdrant reindexed.\n\n"
            "Known Issues: Payment webhook timeouts SDLC-1031. Dashboard p99 latency 400ms.\n\n"
            "API Versioning: v2 current. v1 deprecated 2026-03-01, sunset 2026-07-01."
        ),
    },
]


class MockConfluenceConnector(BaseMCPConnector):
    """In-memory Confluence connector for dev/offline mode."""

    def is_available(self) -> bool:
        return True

    async def get_pages(self, space_key: str) -> list[dict]:
        pages = [
            {"id": p["id"], "title": p["title"], "url": p["url"], "space_key": p["space_key"]}
            for p in _MOCK_PAGES
            if p["space_key"] == space_key
        ]
        logger.debug("MockConfluence.get_pages: %d pages for space '%s'", len(pages), space_key)
        return pages

    async def get_page_content(self, page_id: str) -> str:
        for p in _MOCK_PAGES:
            if p["id"] == page_id:
                return p["content"]
        return ""

    async def get_all_page_texts(self, space_key: str) -> list[dict]:
        results = [
            {"title": p["title"], "content": p["content"], "url": p["url"], "space_key": p["space_key"]}
            for p in _MOCK_PAGES
            if p["space_key"] == space_key
        ]
        logger.debug("MockConfluence.get_all_page_texts: %d pages", len(results))
        return results
