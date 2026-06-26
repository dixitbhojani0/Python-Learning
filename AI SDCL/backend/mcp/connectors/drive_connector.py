"""
backend/mcp/connectors/drive_connector.py

Real Google Drive connector — Google Drive API v3 via httpx.

Auth: an API key (for public files) or OAuth token. Read defensively from
settings via getattr so missing Drive settings never break startup:
    GDRIVE_API_KEY   — simple API key for read-only access to shared files

Auto-selected by MCPRegistry when a key is present AND the connector config sets
use_mock: false. Otherwise the MockDriveConnector is used, so the demo runs with
zero Google setup.

Drive API docs: https://developers.google.com/drive/api/reference/rest/v3
"""
import logging

import httpx

from backend.core.settings import settings
from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_TIMEOUT    = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _api_key() -> str:
    return getattr(settings, "GDRIVE_API_KEY", "") or ""


class DriveConnector(BaseMCPConnector):
    """Real Google Drive connector — method signatures match MockDriveConnector."""

    def is_available(self) -> bool:
        if self.config.get("use_mock", True):
            return False
        return bool(_api_key())

    async def list_files(self, folder: str = "") -> list[dict]:
        q = f"'{folder}' in parents" if folder else None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{_DRIVE_BASE}/files",
                    params={
                        "key":    _api_key(),
                        "q":      q,
                        "fields": "files(id,name,mimeType,size)",
                    },
                )
                r.raise_for_status()
                files = r.json().get("files", [])
                return [
                    {
                        "id":        f.get("id", ""),
                        "name":      f.get("name", ""),
                        "mime_type": f.get("mimeType", ""),
                        "size":      f.get("size", 0),
                    }
                    for f in files
                ]
        except Exception:
            logger.exception("DriveConnector.list_files failed")
            return []

    async def read_file(self, file_id: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{_DRIVE_BASE}/files/{file_id}",
                    params={"key": _api_key(), "alt": "media"},
                )
                r.raise_for_status()
                return r.text
        except Exception:
            logger.exception("DriveConnector.read_file failed for '%s'", file_id)
            return ""


# Self-registration — tells MCPRegistry which classes handle "drive" connectors.
from backend.mcp.registry import MCPRegistry  # noqa: E402
from backend.mcp.connectors.mock_drive import MockDriveConnector  # noqa: E402
MCPRegistry.register("drive", DriveConnector, MockDriveConnector)
