"""
scripts/ingest.py

Executes the RAG Ingestion Pipeline.
Loads all mock files from data/ directory and stores their embeddings in Qdrant.

Usage:
    python scripts/ingest.py [--no-llm]
"""
import argparse
import logging
import sys
from pathlib import Path

# Add project root to python path so we can run from anywhere
sys.path.append(str(Path(__file__).parent.parent.absolute()))

from backend.core.settings import settings
from backend.rag.pipeline import RAGPipeline

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    setup_logging()
    logger = logging.getLogger("ingest_script")

    parser = argparse.ArgumentParser(description="Ingest SDLC knowledge base into Qdrant.")
    parser.add_argument(
        "--no-llm", 
        action="store_true",
        help="Skip LLM contextual prefix generation (faster, doesn't use Groq API)."
    )
    args = parser.parse_args()

    use_llm = not args.no_llm
    if use_llm and (not settings.GROQ_API_KEY or "placeholder" in settings.GROQ_API_KEY):
        logger.warning(
            "GROQ_API_KEY is not configured in .env. "
            "Falling back to --no-llm mode to avoid API errors."
        )
        use_llm = False

    logger.info("Initializing RAG Ingestion Pipeline...")
    logger.info("LLM Contextualization: %s", "ENABLED" if use_llm else "DISABLED (fast mode)")
    logger.info("Qdrant endpoint: %s", settings.QDRANT_URL)

    try:
        pipeline = RAGPipeline(use_llm_context=use_llm)
    except Exception as e:
        logger.exception("Failed to initialize RAG Pipeline. Is Qdrant running?")
        sys.exit(1)

    project = settings.DEFAULT_PROJECT
    data_dir = Path("data")

    # Define directories and their metadata
    ingestion_jobs = [
        {
            "dir": data_dir / "sprint_docs",
            "meta": {"project": project, "source": "local_sprint_docs", "type": "doc"}
        },
        {
            "dir": data_dir / "adr_documents",
            "meta": {"project": project, "source": "local_adr", "type": "adr"}
        },
        {
            "dir": data_dir / "mock_slack",
            "meta": {"project": project, "source": "local_slack_mock", "type": "chat"}
        },
        # v3 doc: new data sources for PR Review + Release Readiness agents
        {
            "dir": data_dir / "version_policies",
            "meta": {"project": project, "source": "local_version_policies", "type": "version_policy"}
        },
        {
            "dir": data_dir / "release_notes",
            "meta": {"project": project, "source": "local_release_notes", "type": "release_note"}
        },
        {
            "dir": data_dir / "incidents",
            "meta": {"project": project, "source": "local_incidents", "type": "incident_report"}
        },
    ]

    total_chunks = 0
    for job in ingestion_jobs:
        directory = job["dir"]
        meta = job["meta"]
        
        if not directory.exists():
            logger.warning("Directory not found: %s. Skipping...", directory)
            continue
            
        logger.info("Ingesting directory: %s", directory)
        try:
            # Mark old chunks stale first so we don't have duplicates
            pipeline.vector_store.mark_stale(project=project, source=meta["source"])
            
            # Run ingestion
            chunks = pipeline.ingest_directory(directory, meta)
            logger.info("Successfully ingested %d chunks from %s", chunks, directory.name)
            total_chunks += chunks
        except Exception as e:
            logger.exception("Error ingesting directory %s", directory)

    logger.info("RAG Ingestion Complete! Total Chunks Stored: %d", total_chunks)
    logger.info("Verify using Qdrant Dashboard or run a search query.")

if __name__ == "__main__":
    main()
