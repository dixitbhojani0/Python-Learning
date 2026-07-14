"""
connectors/confluence_connector.py

Confluence connector — fetches pages from a Confluence space.
Uses the same Atlassian credentials as the Jira connector.
"""
import logging
import re

from core.settings import settings
from connectors.base_connector import BaseMCPConnector, basic_auth_header

logger = logging.getLogger(__name__)


def _is_system_page(title: str) -> bool:
    t = title.lower().strip()
    return (
        t.endswith(" home")
        or t == "home"
        or t.startswith("welcome to")
        or t == "overview"
    )


def _strip_html(html: str) -> str:
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", html, flags=re.DOTALL)
    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<ac:parameter[^>]*>.*?</ac:parameter>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</(p|li|h[1-6]|tr|div|br|ac:plain-text-body|ac:rich-text-body)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"\s*#[0-9A-Fa-f]{6}\b", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ConfluenceConnector(BaseMCPConnector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._base = settings.JIRA_BASE_URL.rstrip("/")
        self._headers = {
            "Authorization": basic_auth_header(settings.JIRA_EMAIL, settings.JIRA_TOKEN),
            "Accept": "application/json",
        }

    def is_available(self) -> bool:
        return bool(
            settings.JIRA_TOKEN
            and settings.JIRA_TOKEN not in ("placeholder",)
            and settings.JIRA_EMAIL not in ("your-email@company.com",)
            and settings.JIRA_BASE_URL not in ("https://your-org.atlassian.net",)
        )

    async def get_pages(self, space_key: str) -> list[dict]:
        url = f"{self._base}/wiki/rest/api/content"
        params = {"spaceKey": space_key, "type": "page", "limit": 50, "expand": "space"}
        try:
            r = await self.http.get(url, params=params)
            r.raise_for_status()
            pages = r.json().get("results", [])
            result = [
                {"id": p["id"], "title": p["title"], "url": f"{self._base}/wiki{p.get('_links', {}).get('webui', '')}", "space_key": space_key}
                for p in pages
            ]
            logger.info("ConfluenceConnector.get_pages: %d pages in space '%s'", len(result), space_key)
            return result
        except Exception:
            # Same contract as every other read: swallow + return empty, never raise —
            # get_all_page_texts calls this unguarded.
            logger.exception("ConfluenceConnector.get_pages: failed for space '%s'", space_key)
            return []

    async def get_page_content(self, page_id: str) -> str:
        url = f"{self._base}/wiki/rest/api/content/{page_id}"
        try:
            r = await self.http.get(url, params={"expand": "body.storage"})
            r.raise_for_status()
            html = r.json().get("body", {}).get("storage", {}).get("value", "")
            return _strip_html(html)
        except Exception:
            logger.exception("ConfluenceConnector.get_page_content: failed for page_id='%s'", page_id)
            return ""

    async def get_all_page_texts(self, space_key: str) -> list[dict]:
        pages = await self.get_pages(space_key)
        results = []
        for page in pages:
            if _is_system_page(page["title"]):
                continue
            content = await self.get_page_content(page["id"])
            if not content.strip():
                continue
            results.append({"title": page["title"], "content": content, "url": page["url"], "space_key": space_key})
        logger.info("ConfluenceConnector.get_all_page_texts: %d/%d pages with content", len(results), len(pages))
        return results

    async def get_page_attachments(self, page_id: str) -> list[dict]:
        url = f"{self._base}/wiki/rest/api/content/{page_id}/child/attachment"
        try:
            r = await self.http.get(url, params={"expand": "metadata.mediaType", "limit": 50})
            r.raise_for_status()
            return [
                {
                    "id":           a["id"],
                    "title":        a["title"],
                    "download_url": f"{self._base}/wiki{a['_links']['download']}",
                    "media_type":   a.get("metadata", {}).get("mediaType", ""),
                }
                for a in r.json().get("results", [])
                if "pdf" in a.get("metadata", {}).get("mediaType", "").lower()
            ]
        except Exception:
            logger.exception("ConfluenceConnector.get_page_attachments: failed for page_id='%s'", page_id)
            return []

    async def download_attachment_bytes(self, download_url: str) -> bytes:
        try:
            r = await self.http.get(download_url)
            r.raise_for_status()
            return r.content
        except Exception:
            logger.exception("ConfluenceConnector.download_attachment_bytes: failed for url='%s'", download_url[:80])
            return b""


from registry import MCPRegistry  # noqa: E402
MCPRegistry.register("confluence", ConfluenceConnector)
