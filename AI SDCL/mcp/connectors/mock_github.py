"""
backend/mcp/connectors/mock_github.py

Mock GitHub connector — returns fake PRs for the SDLC project.

PR data matches the sprint docs and Slack messages: PR-47 (merged DB pool fix),
PR-48 (dashboard API tests, still open), PR-49 (nginx CORS fix, still open).

search_prs()     — keyword search over PR title + description + labels
list_open_prs()  — all PRs that are not yet merged
get_pr_details() — full details for one PR by ID
"""
import logging

from backend.core.settings import settings as _settings
from backend.mcp.base_connector import BaseMCPConnector

_DEFAULT_PROJECT = _settings.DEFAULT_PROJECT

logger = logging.getLogger(__name__)

_MOCK_PRS = [
    {
        "id":           "PR-47",
        "title":        "Fix DB connection pool size in db.yaml",
        "author":       "charlie",
        "status":       "MERGED",
        "repo":         _DEFAULT_PROJECT,
        "branch":       "fix/db-pool-exhaustion",
        "base_branch":  "main",
        "created":      "2026-05-28",
        "merged":       "2026-05-28",
        "description":  "Increased connection pool from 20 to 50 to prevent QueuePool exhaustion. Root cause: nginx config change triggered more concurrent requests than the old pool could handle.",
        "reviewers":    ["alice"],
        "files_changed": ["config/db.yaml"],
        "labels":       ["bugfix", "database", "merged"],
        "ci_status":    "passed",
    },
    {
        "id":           "PR-48",
        "title":        "Dashboard API integration tests",
        "author":       "alice",
        "status":       "OPEN",
        "repo":         _DEFAULT_PROJECT,
        "branch":       "feature/dashboard-api",
        "base_branch":  "main",
        "created":      "2026-05-27",
        "merged":       None,
        "description":  "Integration tests for /api/v2/users dashboard endpoint. Tests verify successful data retrieval and error handling for pool exhaustion scenarios.",
        "reviewers":    ["bob"],
        "files_changed": ["tests/integration/test_dashboard_api.py", "api/v2/users.py"],
        "labels":       ["feature", "dashboard", "tests", "in-review"],
        "ci_status":    "running",
    },
    {
        "id":           "PR-49",
        "title":        "Nginx CORS header fix — permanent solution",
        "author":       "charlie",
        "status":       "OPEN",
        "repo":         _DEFAULT_PROJECT,
        "branch":       "fix/nginx-cors-headers",
        "base_branch":  "main",
        "created":      "2026-05-28",
        "merged":       None,
        "description":  "Permanent fix for CORS headers dropping after nginx reload. Adds explicit `add_header` directives to the server block with `always` flag so headers persist across worker restarts.",
        "reviewers":    ["alice", "diana"],
        "files_changed": ["infrastructure/nginx/nginx.conf"],
        "labels":       ["bugfix", "nginx", "cors", "infrastructure"],
        "ci_status":    "passed",
    },
]


class MockGitHubConnector(BaseMCPConnector):
    """
    Returns fake GitHub PR data for the SDLC project.

    search_prs()     — keyword search over title + description + labels
    list_open_prs()  — all PRs with status != MERGED
    get_pr_details() — single PR lookup by ID
    """

    def is_available(self) -> bool:
        return True   # in-memory — always available

    async def search_prs(self, query: str, repo: str = _DEFAULT_PROJECT) -> list[dict]:
        """Return PRs whose title, description, or labels contain any query word."""
        query_words = query.lower().split()
        results = []
        for pr in _MOCK_PRS:
            if pr["repo"] != repo:
                continue
            searchable = " ".join([
                pr["title"],
                pr["description"],
                " ".join(pr["labels"]),
                pr["status"],
            ]).lower()
            if any(word in searchable for word in query_words):
                results.append(pr)

        logger.debug("MockGitHub.search_prs: query='%s' → %d PRs", query[:50], len(results))
        return results

    async def list_open_prs(self, repo: str = _DEFAULT_PROJECT) -> list[dict]:
        """Return all open (non-merged) PRs for the repo."""
        open_prs = [pr for pr in _MOCK_PRS if pr["repo"] == repo and pr["status"] != "MERGED"]
        logger.debug("MockGitHub.list_open_prs: %d open PRs in '%s'", len(open_prs), repo)
        return open_prs

    async def get_pr_details(self, pr_id: str) -> dict | None:
        """Return full PR details by ID, or None if not found."""
        for pr in _MOCK_PRS:
            if pr["id"] == pr_id:
                return pr
        logger.debug("MockGitHub.get_pr_details: PR '%s' not found", pr_id)
        return None

    async def assign_reviewer(self, pr_id: str, reviewer: str) -> dict:
        """Add a reviewer to the PR (mutates in-memory state so the demo shows it)."""
        for pr in _MOCK_PRS:
            if pr["id"] == pr_id:
                if reviewer not in pr["reviewers"]:
                    pr["reviewers"].append(reviewer)
                logger.info("MockGitHub.assign_reviewer: %s → %s", reviewer, pr_id)
                return {"pr": pr_id, "reviewer": reviewer, "status": "assigned"}
        return {"pr": pr_id, "reviewer": reviewer, "status": "pr_not_found"}

    async def approve_pr(self, pr_id: str, approver: str = "demo-approver") -> dict:
        """
        Approve a PR (mock — marks it APPROVED, does NOT merge).
        Mirrors GitHub's 'submit review with event=APPROVE'.
        """
        for pr in _MOCK_PRS:
            if pr["id"] == pr_id:
                pr["status"]      = "APPROVED"
                pr["approved_by"] = approver
                logger.info("MockGitHub.approve_pr: %s APPROVED by %s", pr_id, approver)
                return {"pr": pr_id, "status": "APPROVED", "approved_by": approver}
        logger.debug("MockGitHub.approve_pr: PR '%s' not found", pr_id)
        return {"pr": pr_id, "status": "pr_not_found"}
