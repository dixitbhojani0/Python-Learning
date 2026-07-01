"""
backend/mcp/connectors/confluence_connector.py

Confluence connector — fetches pages from a Confluence space for RAG ingestion.

Uses the same Atlassian credentials as the Jira connector:
  JIRA_BASE_URL   — e.g. https://your-org.atlassian.net
  JIRA_EMAIL      — Atlassian account email
  JIRA_TOKEN      — Atlassian API token (same token works for both Jira and Confluence)

Confluence REST API docs:
  https://developer.atlassian.com/cloud/confluence/rest/v1/intro/
"""
import base64
import logging
import re

import httpx

from backend.core.settings import settings
from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _is_system_page(title: str) -> bool:
    """
    Return True for auto-generated Confluence pages that contain no project content.
    These are skipped during ingest so they don't pollute RAG search results.
    """
    t = title.lower().strip()
    return (
        t.endswith(" home")               # "SDLC-TEST Home", "Project Home"
        or t == "home"
        or t.startswith("welcome to")     # "Welcome to your new space!"
        or t == "overview"                # common empty placeholder page
    )


def _basic_auth_header(email: str, token: str) -> str:
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {encoded}"


def _strip_html(html: str) -> str:
    """Strip HTML tags from Confluence storage format, preserving meaningful whitespace."""
    # Step 1: Extract CDATA content BEFORE any tag stripping.
    # Confluence code blocks store content as: <ac:plain-text-body><![CDATA[text]]></ac:plain-text-body>
    # The regex <[^>]+> would strip from "<" to the first ">" — eating the content.
    # Extracting CDATA first preserves the actual text.
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", html, flags=re.DOTALL)

    # Step 2: Remove script/style blocks entirely
    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Step 3: Remove Confluence macro parameter values (color codes, language hints, etc.)
    text = re.sub(r"<ac:parameter[^>]*>.*?</ac:parameter>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Step 4: Replace block-level tags with newlines for readability
    text = re.sub(r"</(p|li|h[1-6]|tr|div|br|ac:plain-text-body|ac:rich-text-body)>", "\n", text, flags=re.IGNORECASE)

    # Step 5: Strip all remaining tags (HTML + Confluence ac:/ri: namespace elements)
    text = re.sub(r"<[^>]+>", "", text)

    # Step 6: Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')

    # Step 7: Remove orphaned CSS hex color codes left by Confluence macro stripping (e.g. "#E3FCEF")
    text = re.sub(r"\s*#[0-9A-Fa-f]{6}\b", "", text)

    # Step 8: Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ConfluenceConnector(BaseMCPConnector):
    """
    Fetches Confluence pages by space key for RAG ingestion.

    is_available() returns True when JIRA_TOKEN is set (same auth as Jira).
    Falls back to MockConfluenceConnector when credentials are placeholders.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._base = settings.JIRA_BASE_URL.rstrip("/")
        self._headers = {
            "Authorization": _basic_auth_header(settings.JIRA_EMAIL, settings.JIRA_TOKEN),
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
        """
        Return all pages in a Confluence space.

        Each page dict: {id, title, url, space_key}
        Content is NOT included here — call get_page_content() per page.

        Falls back to mock on 401/403/404.
        """
        url = f"{self._base}/wiki/rest/api/content"
        params = {
            "spaceKey": space_key,
            "type": "page",
            "limit": 50,
            "expand": "space",
        }
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
            pages = r.json().get("results", [])
            result = [
                {
                    "id":         p["id"],
                    "title":      p["title"],
                    "url":        f"{self._base}/wiki{p.get('_links', {}).get('webui', '')}",
                    "space_key":  space_key,
                }
                for p in pages
            ]
            logger.info("ConfluenceConnector.get_pages: %d pages in space '%s'", len(result), space_key)
            return result
        except httpx.HTTPStatusError as exc:
            logger.exception("ConfluenceConnector.get_pages: HTTP error %d", exc.response.status_code)
            raise
        except Exception as exc:
            logger.exception("ConfluenceConnector.get_pages: request failed for space '%s'", space_key)
            raise exc

    async def get_page_content(self, page_id: str) -> str:
        """
        Fetch the full text content of a single page (HTML stripped to plain text).

        Returns empty string on any failure — caller can skip gracefully.
        """
        url = f"{self._base}/wiki/rest/api/content/{page_id}"
        params = {"expand": "body.storage"}
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
            html = r.json().get("body", {}).get("storage", {}).get("value", "")
            return _strip_html(html)
        except Exception:
            logger.exception("ConfluenceConnector.get_page_content: failed for page_id='%s'", page_id)
            return ""

    async def get_all_page_texts(self, space_key: str) -> list[dict]:
        """
        Convenience method — fetch all pages + their text content in one call.

        Returns list of {title, content, url, space_key} ready for RAG ingestion.
        Pages with empty content after HTML strip are skipped.
        """
        pages = await self.get_pages(space_key)
        results = []
        for page in pages:
            if _is_system_page(page["title"]):
                logger.debug("ConfluenceConnector: skipping system page '%s'", page["title"])
                continue
            content = await self.get_page_content(page["id"])
            if not content.strip():
                logger.debug("ConfluenceConnector: skipping empty page '%s'", page["title"])
                continue
            results.append({
                "title":      page["title"],
                "content":    content,
                "url":        page["url"],
                "space_key":  space_key,
            })
        logger.info(
            "ConfluenceConnector.get_all_page_texts: %d/%d pages with content",
            len(results), len(pages),
        )
        return results

    async def get_page_attachments(self, page_id: str) -> list[dict]:
        """
        Return all PDF attachments for a page.

        Each dict: {id, title, download_url, media_type}
        Only PDF attachments are returned — images and other files are skipped.
        """
        url = f"{self._base}/wiki/rest/api/content/{page_id}/child/attachment"
        params = {"expand": "metadata.mediaType", "limit": 50}
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
            attachments = r.json().get("results", [])
            pdf_attachments = [
                {
                    "id":           a["id"],
                    "title":        a["title"],
                    "download_url": f"{self._base}/wiki{a['_links']['download']}",
                    "media_type":   a.get("metadata", {}).get("mediaType", ""),
                }
                for a in attachments
                if "pdf" in a.get("metadata", {}).get("mediaType", "").lower()
            ]
            logger.info(
                "ConfluenceConnector.get_page_attachments: %d PDF(s) in page '%s'",
                len(pdf_attachments), page_id,
            )
            return pdf_attachments
        except Exception:
            logger.exception("ConfluenceConnector.get_page_attachments: failed for page_id='%s'", page_id)
            return []

    async def _get_page_version(self, page_id: str) -> int:
        """Fetch current page version number — required by the update API."""
        url = f"{self._base}/wiki/rest/api/content/{page_id}"
        async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
            r = await client.get(url, params={"expand": "version"})
            r.raise_for_status()
        return r.json().get("version", {}).get("number", 1)

    async def create_page(self, space_key: str, title: str, body_html: str, parent_id: str = "") -> dict:
        """
        Create a new Confluence page in the given space.
        body_html should be valid Confluence storage format (simple HTML is fine).
        Returns {id, title, url} or empty dict on failure.
        """
        payload: dict = {
            "type":  "page",
            "title": title,
            "space": {"key": space_key},
            "body":  {
                "storage": {
                    "value":          body_html,
                    "representation": "storage",
                }
            },
        }
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]

        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=_TIMEOUT) as client:
                r = await client.post(f"{self._base}/wiki/rest/api/content", json=payload)
                r.raise_for_status()
            data = r.json()
            page_id = data.get("id", "")
            url = f"{self._base}/wiki{data.get('_links', {}).get('webui', '')}"
            logger.info("ConfluenceConnector.create_page: created '%s' (id=%s)", title, page_id)
            return {"id": page_id, "title": title, "url": url}
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "ConfluenceConnector.create_page: HTTP %d — %s",
                exc.response.status_code, exc.response.text[:300],
            )
            return {}
        except Exception:
            logger.exception("ConfluenceConnector.create_page: failed for title='%s'", title)
            return {}

    async def update_page(self, page_id: str, title: str, body_html: str) -> dict:
        """
        Update an existing Confluence page. Auto-fetches the current version number.
        Returns {id, title, url} or empty dict on failure.
        """
        try:
            version = await self._get_page_version(page_id)
        except Exception:
            logger.exception("ConfluenceConnector.update_page: could not fetch version for page_id='%s'", page_id)
            return {}

        payload = {
            "id":      page_id,
            "type":    "page",
            "title":   title,
            "version": {"number": version + 1},
            "body":    {
                "storage": {
                    "value":          body_html,
                    "representation": "storage",
                }
            },
        }
        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=_TIMEOUT) as client:
                r = await client.put(f"{self._base}/wiki/rest/api/content/{page_id}", json=payload)
                r.raise_for_status()
            data = r.json()
            url = f"{self._base}/wiki{data.get('_links', {}).get('webui', '')}"
            logger.info("ConfluenceConnector.update_page: updated '%s' (id=%s)", title, page_id)
            return {"id": page_id, "title": title, "url": url}
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "ConfluenceConnector.update_page: HTTP %d — %s",
                exc.response.status_code, exc.response.text[:300],
            )
            return {}
        except Exception:
            logger.exception("ConfluenceConnector.update_page: failed for page_id='%s'", page_id)
            return {}

    async def upload_attachment(self, page_id: str, file_path: str) -> dict:
        """
        Upload a file as an attachment to a Confluence page.
        If an attachment with the same filename already exists it is replaced.
        Returns {id, title, download_url} or empty dict on failure.

        Confluence requires X-Atlassian-Token: no-check to bypass XSRF on attachment uploads.
        """
        from pathlib import Path
        import mimetypes

        path = Path(file_path)
        if not path.exists():
            logger.error("ConfluenceConnector.upload_attachment: file not found — %s", file_path)
            return {}

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        url = f"{self._base}/wiki/rest/api/content/{page_id}/child/attachment"

        # X-Atlassian-Token: no-check is required — without it Confluence rejects with 403 XSRF
        upload_headers = {
            "Authorization": self._headers["Authorization"],
            "X-Atlassian-Token": "no-check",
            "Accept": "application/json",
        }
        try:
            with open(path, "rb") as f:
                files = {"file": (path.name, f, mime_type)}
                data = {"minorEdit": "true"}
                async with httpx.AsyncClient(headers=upload_headers, timeout=httpx.Timeout(60.0)) as client:
                    r = await client.post(url, files=files, data=data)
                    r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                att = results[0]
                download_url = f"{self._base}/wiki{att.get('_links', {}).get('download', '')}"
                logger.info("ConfluenceConnector.upload_attachment: uploaded '%s' to page %s", path.name, page_id)
                return {"id": att["id"], "title": att["title"], "download_url": download_url}
            return {}
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "ConfluenceConnector.upload_attachment: HTTP %d — %s",
                exc.response.status_code, exc.response.text[:300],
            )
            return {}
        except Exception:
            logger.exception("ConfluenceConnector.upload_attachment: failed for '%s'", path.name)
            return {}

    async def download_attachment_bytes(self, download_url: str) -> bytes:
        """
        Download an attachment and return its raw bytes.
        The caller writes these to a temp file for unstructured.io processing.

        Confluence Cloud redirects /download to api.media.atlassian.com with a
        signed token — follow_redirects=True is required or httpx raises on 302.
        """
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT, follow_redirects=True) as client:
                r = await client.get(download_url)
                r.raise_for_status()
            return r.content
        except Exception:
            logger.exception("ConfluenceConnector.download_attachment_bytes: failed for url='%s'", download_url[:80])
            return b""


# Self-registration — tells MCPRegistry which classes handle "confluence" connectors.
from backend.mcp.registry import MCPRegistry  # noqa: E402
MCPRegistry.register("confluence", ConfluenceConnector)
