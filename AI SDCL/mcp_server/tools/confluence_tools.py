"""
backend/mcp_server/tools/confluence_tools.py

Confluence READ tools over MCP, delegating to the existing ConfluenceConnector
(real Atlassian Confluence when creds are set — same auth as Jira — else mock).
Confluence pages are also ingested into RAG; these tools are for *live* page
lookup by id/space. Page create/update are WRITES (added later, behind HITL).
"""
import logging
from typing import Any

from backend.core.settings import settings

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add Confluence read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def confluence_get_pages(space_key: str = "") -> list[dict]:
        """List Confluence pages in a space.

        Use to discover what documentation exists (ADRs, policies, runbooks)
        before fetching a specific page's content.

        Args:
            space_key: Confluence space (default = configured space, e.g. "SDLC").

        Returns: list of pages, each with id and title.
        """
        space = space_key or settings.CONFLUENCE_SPACE_KEY
        logger.info("tool confluence_get_pages(space_key=%r)", space)
        return await registry.get("confluence").get_pages(space)

    @mcp.tool()
    async def confluence_get_page_content(page_id: str) -> str:
        """Fetch the full text of one Confluence page by its id.

        Use after confluence_get_pages to read a specific document live (not the
        possibly-stale RAG copy).

        Args:
            page_id: the Confluence page id.

        Returns: the page's plain-text content.
        """
        logger.info("tool confluence_get_page_content(page_id=%r)", page_id)
        return await registry.get("confluence").get_page_content(page_id)

    logger.info("confluence_tools: registered 2 read tools")
