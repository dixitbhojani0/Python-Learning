"""
backend/mcp/connectors/mock_drive.py

Mock Google Drive connector — reads from data/mock_drive/*.

Drive is the document source the RAG ingestion pipeline can pull from
(Solution Document Section 17). The mock lists and reads local files so the
connector is fully demonstrable without Google credentials.

list_files()  — list available documents (name, id, mime type)
read_file()   — return the text content of a document by name or id
"""
import logging
from pathlib import Path

from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_MOCK_DATA_DIR = Path(__file__).parents[3] / "data" / "mock_drive"
_TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json"}


class MockDriveConnector(BaseMCPConnector):
    """Returns Google Drive documents from a local folder."""

    def is_available(self) -> bool:
        return _MOCK_DATA_DIR.exists()

    async def list_files(self, folder: str = "") -> list[dict]:
        """List documents available in the mock Drive folder."""
        if not _MOCK_DATA_DIR.exists():
            return []
        files = [
            {
                "id":        p.name,            # filename doubles as id in the mock
                "name":      p.name,
                "mime_type": "text/markdown" if p.suffix == ".md" else "text/plain",
                "size":      p.stat().st_size,
            }
            for p in sorted(_MOCK_DATA_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES
        ]
        logger.debug("MockDrive.list_files: %d files", len(files))
        return files

    async def read_file(self, file_id: str) -> str:
        """Return the text content of a document by name/id. '' if not found."""
        path = _MOCK_DATA_DIR / Path(file_id).name   # Path().name guards traversal
        try:
            if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES:
                return path.read_text(encoding="utf-8")
            logger.warning("MockDrive.read_file: '%s' not found", file_id)
            return ""
        except Exception as exc:
            logger.warning("MockDrive.read_file: error reading %s — %s", file_id, exc)
            return ""
