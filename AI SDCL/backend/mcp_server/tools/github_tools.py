"""
backend/mcp_server/tools/github_tools.py

GitHub READ tools, exposed over MCP. Each delegates to the existing
GitHubConnector (real GitHub REST, or mock when no token) via the shared
registry. PR write actions (assign reviewer / approve) are added in Step 4,
behind HITL.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add GitHub read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def github_list_open_prs(repo: str = "") -> list[dict]:
        """List currently open pull requests.

        Use for "what PRs are open", release readiness, or PR-risk analysis.

        Args:
            repo: "owner/name"; empty = default repo from config.

        Returns: list of PRs, each with id, title, author, ci_status, reviewers,
        and state.
        """
        logger.info("tool github_list_open_prs(repo=%r)", repo)
        return await registry.get("github").list_open_prs(repo)

    @mcp.tool()
    async def github_search_prs(query: str, repo: str = "") -> list[dict]:
        """Search pull requests by natural language / keywords.

        Use for "the PR about the dashboard", "PRs touching auth".

        Args:
            query: keywords or PR title text.
            repo: "owner/name"; empty = default repo.

        Returns: list of matching PRs (same fields as github_list_open_prs).
        """
        logger.info("tool github_search_prs(query=%r, repo=%r)", query, repo)
        return await registry.get("github").search_prs(query, repo)

    @mcp.tool()
    async def github_get_pr_details(pr_id: str) -> dict:
        """Fetch one pull request's details by its id/number (e.g. "PR-49" or "49").

        Use when the user names a specific PR.

        Args:
            pr_id: the PR identifier, e.g. "PR-49".

        Returns: the PR dict, or {"error": ..., "pr_id": ...} if not found.
        """
        logger.info("tool github_get_pr_details(pr_id=%r)", pr_id)
        pr = await registry.get("github").get_pr_details(pr_id)
        return pr if pr else {"error": "pr not found", "pr_id": pr_id}

    logger.info("github_tools: registered 3 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """
    Add GitHub WRITE tools (state-changing). Excluded from the autonomous gather
    loop; run only via the approved HITL execution path.
    """

    @mcp.tool()
    async def github_assign_reviewer(pr_id: str, reviewer: str) -> dict:
        """Request a reviewer on a pull request. WRITE — requires HITL approval.

        Args:
            pr_id: PR identifier, e.g. "PR-49".
            reviewer: GitHub username to request.

        Returns: {"pr": ..., "reviewer": ..., "status": ...}.
        """
        logger.info("tool github_assign_reviewer(pr_id=%r, reviewer=%r)", pr_id, reviewer)
        return await registry.get("github").assign_reviewer(pr_id, reviewer)

    @mcp.tool()
    async def github_approve_pr(pr_id: str, approver: str = "") -> dict:
        """Submit an APPROVE review on a pull request (does NOT merge). WRITE — requires HITL approval.

        Args:
            pr_id: PR identifier, e.g. "PR-49".
            approver: name recorded as the approver.

        Returns: {"pr": ..., "status": "APPROVED", ...}.
        """
        logger.info("tool github_approve_pr(pr_id=%r)", pr_id)
        return await registry.get("github").approve_pr(pr_id, approver=approver)

    @mcp.tool()
    async def github_request_changes_pr(pr_id: str, body: str = "", reviewer: str = "") -> dict:
        """Submit a REQUEST_CHANGES review on a pull request. Blocks merge until author addresses feedback. WRITE — requires HITL approval.

        Args:
            pr_id: PR identifier, e.g. "PR-49".
            body: review comment explaining what needs to change.
            reviewer: name recorded as the reviewer requesting changes.

        Returns: {"pr": ..., "status": "CHANGES_REQUESTED", ...}.
        """
        logger.info("tool github_request_changes_pr(pr_id=%r)", pr_id)
        return await registry.get("github").request_changes_pr(pr_id, body=body, reviewer=reviewer)

    logger.info("github_tools: registered 3 write tools")
