"""
tools/github_tools.py

GitHub READ and WRITE tools exposed over MCP.
"""
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PR_ID_RE = re.compile(r"^(?:PR-|#)?(\d+)$", re.IGNORECASE)


def _invalid_pr_id(pr_id: str) -> dict | None:
    """Validate at the tool boundary: accept 'PR-49', '#49', or '49'.
    Returns an error dict for bad input, None when valid — a garbage pr_id
    otherwise becomes a malformed GitHub URL and a confusing 404 downstream."""
    if _PR_ID_RE.match(pr_id.strip()):
        return None
    return {
        "success": False, "status": "error",
        "error": f"invalid pr_id {pr_id!r} — expected 'PR-<number>' or a bare number",
        "pr_id": pr_id,
    }


def register(mcp: Any, registry: Any) -> None:
    """Add GitHub read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def github_list_open_prs(repo: str = "") -> list[dict]:
        """List currently open pull requests.

        Args:
            repo: "owner/name"; empty = default repo from config.

        Returns: list of PRs with id, title, author, ci_status, reviewers, state.
        """
        logger.info("tool github_list_open_prs(repo=%r)", repo)
        return await registry.get("github").list_open_prs(repo)

    @mcp.tool()
    async def github_search_prs(query: str, repo: str = "") -> list[dict]:
        """Search pull requests by natural language / keywords.

        Args:
            query: keywords or PR title text.
            repo: "owner/name"; empty = default repo.

        Returns: list of matching PRs.
        """
        logger.info("tool github_search_prs(query=%r, repo=%r)", query, repo)
        return await registry.get("github").search_prs(query, repo)

    @mcp.tool()
    async def github_get_pr_details(pr_id: str) -> dict:
        """Fetch one pull request's details by its id/number (e.g. "PR-49" or "49").

        Args:
            pr_id: the PR identifier, e.g. "PR-49".

        Returns: the PR dict, or {"error": ..., "pr_id": ...} if not found.
        """
        logger.info("tool github_get_pr_details(pr_id=%r)", pr_id)
        if err := _invalid_pr_id(pr_id):
            return err
        pr = await registry.get("github").get_pr_details(pr_id)
        return pr if pr else {"error": "pr not found", "pr_id": pr_id}

    @mcp.tool()
    async def github_is_collaborator(username: str) -> dict:
        """Check whether `username` is a collaborator on the repo.

        Use this BEFORE proposing to assign someone as a reviewer — GitHub's
        assign-reviewer API silently ignores a nonexistent/unreachable username
        instead of erroring, so this is the only reliable way to catch it upfront.

        Args:
            username: GitHub username to check.

        Returns: {"username": str, "is_collaborator": bool}.
        """
        logger.info("tool github_is_collaborator(username=%r)", username)
        is_collab = await registry.get("github").is_collaborator(username)
        return {"username": username, "is_collaborator": is_collab}

    logger.info("github_tools: registered 4 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """Add GitHub WRITE tools. Run only via HITL approval."""

    @mcp.tool()
    async def github_assign_reviewer(pr_id: str, reviewer: str) -> dict:
        """Request a reviewer on a pull request. WRITE — requires HITL approval.

        The collaborator check runs here, not just in the docstring: GitHub's API
        silently omits an invalid reviewer instead of erroring, so writing first
        and checking after gives a confusing partial failure.

        Args:
            pr_id: PR identifier, e.g. "PR-49".
            reviewer: GitHub username to request.
        """
        logger.info("tool github_assign_reviewer(pr_id=%r, reviewer=%r)", pr_id, reviewer)
        if err := _invalid_pr_id(pr_id):
            return err
        github = registry.get("github")
        if not await github.is_collaborator(reviewer):
            return {
                "success": False, "status": "error", "pr": pr_id, "reviewer": reviewer,
                "error": f"'{reviewer}' is not a collaborator on this repository — "
                         f"add them as a collaborator first, or choose someone else.",
            }
        return await github.assign_reviewer(pr_id, reviewer)

    @mcp.tool()
    async def github_approve_pr(pr_id: str, approver: str = "") -> dict:
        """Submit an APPROVE review on a pull request (does NOT merge). WRITE — requires HITL approval.

        Args:
            pr_id: PR identifier, e.g. "PR-49".
            approver: audit label echoed back in the result. NOT sent to GitHub —
                the review is always attributed to the server's token owner.
        """
        logger.info("tool github_approve_pr(pr_id=%r)", pr_id)
        if err := _invalid_pr_id(pr_id):
            return err
        return await registry.get("github").approve_pr(pr_id, approver=approver)

    @mcp.tool()
    async def github_request_changes_pr(pr_id: str, body: str = "", reviewer: str = "") -> dict:
        """Submit a REQUEST_CHANGES review on a pull request. WRITE — requires HITL approval.

        Args:
            pr_id: PR identifier, e.g. "PR-49".
            body: review comment explaining what needs to change.
            reviewer: audit label echoed back in the result. NOT sent to GitHub —
                the review is always attributed to the server's token owner.
        """
        logger.info("tool github_request_changes_pr(pr_id=%r)", pr_id)
        if err := _invalid_pr_id(pr_id):
            return err
        return await registry.get("github").request_changes_pr(pr_id, body=body, reviewer=reviewer)

    logger.info("github_tools: registered 3 write tools")
