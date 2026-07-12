"""
scripts/ingest.py

Ingests local data/ files into Qdrant.
Use this when you want to add or refresh chunks from local markdown/PDF files.

Usage:
    python scripts/ingest.py           # ingest all data/ subdirectories
    python scripts/ingest.py --no-llm  # skip LLM contextual prefix (faster)

For Confluence ingestion, use: python scripts/ingest_confluence.py
"""
import argparse
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


def main():
    setup_logging()
    logger = logging.getLogger("ingest_script")

    parser = argparse.ArgumentParser(description="Ingest local data/ files into Qdrant.")
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

    logger.info("Initializing RAG Ingestion Pipeline...")
    logger.info("LLM Contextualization: %s", "ENABLED" if use_llm else "DISABLED (fast mode)")
    logger.info("Qdrant endpoint: %s", settings.QDRANT_URL)

    try:
        pipeline = RAGPipeline(use_llm_context=use_llm)
    except Exception:
        logger.exception("Failed to initialize RAGPipeline — is Qdrant running?")
        sys.exit(1)

    project  = settings.DEFAULT_PROJECT
    data_dir = Path(__file__).parent.parent / "data"

    ingestion_jobs = [
        {"dir": data_dir / "sprint_docs",      "source": "local_sprint_docs",    "type": "doc"},
        {"dir": data_dir / "adr_documents",    "source": "local_adr",            "type": "adr"},
        {"dir": data_dir / "incidents",        "source": "local_incidents",      "type": "incident_report"},
        {"dir": data_dir / "version_policies", "source": "local_version_policy", "type": "version_policy"},
        {"dir": data_dir / "release_notes",    "source": "local_release_notes",  "type": "release_note"},
        {"dir": data_dir / "coding_standards", "source": "local_coding_standards","type": "doc"},
        # mock_slack excluded — use live Slack MCP when SLACK_TOKEN is configured
    ]

    total = 0
    for job in ingestion_jobs:
        directory = Path(job["dir"])
        if not directory.exists():
            logger.warning("Directory not found: %s — skipping", directory)
            continue
        meta = {"project": project, "source": job["source"], "type": job["type"]}
        pipeline.vector_store.mark_stale(project=project, source=job["source"])
        chunks = pipeline.ingest_directory(directory, meta)
        logger.info("Ingested %d chunks from %s", chunks, directory.name)
        total += chunks

    logger.info("Local ingestion complete — %d total chunks stored.", total)


if __name__ == "__main__":
    main()
