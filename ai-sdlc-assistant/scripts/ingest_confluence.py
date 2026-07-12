"""
scripts/ingest_confluence.py

Ingests all pages from the configured Confluence space into Qdrant.
Run this after adding or updating pages in Confluence.

Usage:
    python scripts/ingest_confluence.py           # ingest with LLM contextual prefix
    python scripts/ingest_confluence.py --no-llm  # faster, skips Groq API calls

All pages land under source='confluence_live' in Qdrant.
UUID5 content hashing ensures re-running this is safe — identical pages upsert in place.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.absolute()))

from backend.core.settings import settings
from backend.rag.pipeline import RAGPipeline


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


_DOC_TYPE_MAP = [
    ("INC-",         "incident_report"),
    ("ADR-",         "adr"),
    ("Release Note", "release_note"),
    ("Sprint",       "doc"),
    ("Semantic Ver", "version_policy"),
    ("Versioning",   "version_policy"),
    ("Clean Code",   "doc"),
]


def _infer_doc_type(title: str) -> str:
    for prefix, doc_type in _DOC_TYPE_MAP:
        if title.startswith(prefix):
            return doc_type
    return "doc"


async def run(pipeline: RAGPipeline) -> int:
    from backend.mcp.connectors.confluence_connector import ConfluenceConnector

    project   = settings.DEFAULT_PROJECT
    space_key = settings.CONFLUENCE_SPACE_KEY
    source    = "confluence_live"

    conf = ConfluenceConnector(name="confluence", connector_config={})
    if not conf.is_available():
        logging.getLogger("ingest_confluence").error(
            "Confluence not available — check JIRA_TOKEN / JIRA_BASE_URL / JIRA_EMAIL in .env"
        )
        return 0

    pages = await conf.get_all_page_texts(space_key)
    if not pages:
        logging.getLogger("ingest_confluence").warning(
            "No pages returned from Confluence space '%s'", space_key
        )
        return 0

    logger = logging.getLogger("ingest_confluence")
    logger.info("%d pages fetched from space '%s'", len(pages), space_key)

    # Mark all previous confluence_live chunks stale so removed/renamed pages don't linger.
    # Unchanged pages are immediately re-upserted with the same UUID5 ID — no duplication.
    pipeline.vector_store.mark_stale(project=project, source=source)

    total = 0
    for page in pages:
        title    = page["title"]
        content  = page["content"]
        doc_type = _infer_doc_type(title)
        meta = {
            "project":   project,
            "source":    source,
            "type":      doc_type,
            "url":       page.get("url", ""),
            "space_key": page.get("space_key", space_key),
        }
        chunks = pipeline._ingest_text(content, title, doc_type, meta)
        logger.info("'%s' -> %d chunks (type=%s)", title, chunks, doc_type)
        total += chunks

    return total


def main():
    setup_logging()
    logger = logging.getLogger("ingest_confluence")

    parser = argparse.ArgumentParser(description="Ingest Confluence pages into Qdrant.")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM contextual prefix generation (faster, does not use Groq API).",
    )
    args = parser.parse_args()

    use_llm = not args.no_llm
    if use_llm and (not settings.GROQ_API_KEY or "placeholder" in settings.GROQ_API_KEY):
        logger.warning("GROQ_API_KEY not configured — falling back to --no-llm mode")
        use_llm = False

    logger.info(
        "Space: %s | LLM contextualization: %s | Qdrant: %s",
        settings.CONFLUENCE_SPACE_KEY,
        "ON" if use_llm else "OFF (fast mode)",
        settings.QDRANT_URL,
    )

    try:
        pipeline = RAGPipeline(use_llm_context=use_llm)
    except Exception:
        logger.exception("Failed to initialize RAGPipeline — is Qdrant running?")
        sys.exit(1)

    total = asyncio.run(run(pipeline))
    logger.info("Confluence ingestion complete — %d chunks stored in Qdrant.", total)
    logger.info("Source tag: confluence_live | Space: %s", settings.CONFLUENCE_SPACE_KEY)


if __name__ == "__main__":
    main()
