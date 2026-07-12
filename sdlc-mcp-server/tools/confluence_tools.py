"""
tools/confluence_tools.py

Confluence READ tools over MCP.
"""
import logging
from typing import Any

from core.settings import settings

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add Confluence read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def confluence_get_pages(space_key: str = "") -> list[dict]:
        """List all Confluence pages in a space (id, title, url).

        Args:
            space_key: Confluence space key (default = configured CONFLUENCE_SPACE_KEY).
        """
        space = space_key or settings.CONFLUENCE_SPACE_KEY
        logger.info("tool confluence_get_pages(space_key=%r)", space)
        return await registry.get("confluence").get_pages(space)

    @mcp.tool()
    async def confluence_get_page_content(page_id: str) -> str:
        """Fetch the plain-text content of a single Confluence page.

        Args:
            page_id: the Confluence page id.
        """
        logger.info("tool confluence_get_page_content(page_id=%r)", page_id)
        return await registry.get("confluence").get_page_content(page_id)

    @mcp.tool()
    async def confluence_get_all_page_texts(space_key: str = "") -> list[dict]:
        """Fetch all non-empty, non-system pages from a Confluence space with their text.

        Returns: list of {title, content, url, space_key}. System/empty pages are skipped.

        Args:
            space_key: Confluence space key (default = configured CONFLUENCE_SPACE_KEY).
        """
        space = space_key or settings.CONFLUENCE_SPACE_KEY
        logger.info("tool confluence_get_all_page_texts(space_key=%r)", space)
        return await registry.get("confluence").get_all_page_texts(space)

    @mcp.tool()
    async def confluence_get_page_attachments(page_id: str) -> list[dict]:
        """List PDF attachments for a Confluence page.

        Returns: list of {id, title, download_url, media_type}.

        Args:
            page_id: the Confluence page id.
        """
        logger.info("tool confluence_get_page_attachments(page_id=%r)", page_id)
        return await registry.get("confluence").get_page_attachments(page_id)

    @mcp.tool()
    async def confluence_download_attachment(download_url: str) -> str:
        """Download a Confluence attachment and return its contents as a base64-encoded string.

        Args:
            download_url: the download_url from confluence_get_page_attachments.

        Returns: base64-encoded file bytes, or empty string on failure.
        """
        import base64
        logger.info("tool confluence_download_attachment(url=%r)", download_url[:60])
        data = await registry.get("confluence").download_attachment_bytes(download_url)
        return base64.b64encode(data).decode() if data else ""

    logger.info("confluence_tools: registered 5 read tools")


def register_writes(mcp: Any, registry: Any) -> None:
    """Confluence write tools — not yet exposed (add page create/update here when needed)."""
    pass
