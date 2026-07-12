"""
backend/rag/validators.py

Validation helpers for the RAG ingestion pipeline.

Called before storing any chunk in Qdrant to catch problems early:
  - Invalid embeddings (wrong dimension, all-zero vectors)
  - Missing required metadata fields

Keeping validation separate from orchestration logic makes it easy
to unit-test these guards in isolation.
"""
import logging

logger = logging.getLogger(__name__)

# Required metadata fields — chunks missing these are silently broken at retrieval time
_REQUIRED_METADATA = {"project", "source", "type"}


def validate_embedding(embedding: list[float], expected_dim: int = 384) -> bool:
    """
    Sanity-check the embedding vector before storing it.

    All-zero vector: happens when embed_text() receives empty/whitespace input.
    It matches EVERYTHING equally under cosine similarity — corrupts search.

    Wrong dimension: model mismatch between ingestion and query time.
    Would cause all searches to return wrong results silently.
    """
    if len(embedding) != expected_dim:
        return False
    if all(v == 0.0 for v in embedding):
        return False
    return True


def validate_metadata(metadata: dict) -> bool:
    """
    Check that all required metadata fields are present before storing.

    'project' — used by Qdrant pre-filter on every search. Missing = chunk invisible.
    'source'  — used by mark_stale() to clean up old chunks on re-ingestion.
    'type'    — used by agents to filter by document type (e.g. only 'version_policy').
    """
    missing = _REQUIRED_METADATA - metadata.keys()
    if missing:
        logger.warning("Pipeline: chunk missing required metadata fields %s — skipping", missing)
        return False
    return True
