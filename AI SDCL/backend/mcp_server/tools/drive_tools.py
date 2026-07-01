"""
backend/mcp_server/tools/drive_tools.py

Google Drive READ tools over MCP, delegating to the existing DriveConnector
(mock by default unless Google credentials are configured).
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(mcp: Any, registry: Any) -> None:
    """Add Drive read tools to the FastMCP server `mcp`, backed by `registry`."""

    @mcp.tool()
    async def drive_list_files(folder: str = "") -> list[dict]:
        """List files available in Google Drive (optionally within a folder).

        Use to discover shared documents/specs before reading one.

        Args:
            folder: folder name/id to scope to; empty = top level.

        Returns: list of files, each with id, name, and type/mime.
        """
        logger.info("tool drive_list_files(folder=%r)", folder)
        return await registry.get("drive").list_files(folder)

    @mcp.tool()
    async def drive_read_file(file_id: str) -> str:
        """Read the text content of one Google Drive file by id.

        Args:
            file_id: the Drive file id (from drive_list_files).

        Returns: the file's extracted text.
        """
        logger.info("tool drive_read_file(file_id=%r)", file_id)
        return await registry.get("drive").read_file(file_id)

    logger.info("drive_tools: registered 2 read tools")
