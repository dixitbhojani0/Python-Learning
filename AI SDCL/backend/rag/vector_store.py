"""
backend/rag/vector_store.py

Qdrant wrapper — handles create, upsert, and search operations.
All other RAG components talk to Qdrant through this class only.

Official Qdrant Python client docs:
https://python-client.qdrant.tech/

Why Qdrant?
- Runs locally in Docker (free, no cloud needed)
- Supports both dense vectors (semantic) and sparse (BM25)
- Metadata filtering — filter by project before searching
- Built-in cosine similarity
"""
import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

from backend.core.settings import settings
from backend.core.config_loader import config

logger = logging.getLogger(__name__)


class VectorStore:
    """
    Thin wrapper around QdrantClient.
    Provides: create_collection, upsert, search, delete_stale.
    """

    def __init__(self):
        self.client = QdrantClient(url=settings.QDRANT_URL)
        rag_cfg = config.get_rag_config()
        self.collection = rag_cfg.get("vector_store", {}).get(
            "collection_name", settings.QDRANT_COLLECTION
        )
        self.vector_dim = rag_cfg.get("embeddings", {}).get("dimension", 384)
        self._ensure_collection()

    def _ensure_collection(self):
        """
        Create the Qdrant collection if it doesn't already exist.
        Called once at startup — idempotent (safe to call multiple times).
        """
        existing = [c.name for c in self.client.get_collections().collections]
        if self.collection not in existing:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=self.vector_dim,
                    distance=Distance.COSINE,    # cosine similarity for semantic search
                ),
            )
            logger.info("VectorStore: created collection '%s' (dim=%d)", self.collection, self.vector_dim)
        else:
            logger.debug("VectorStore: collection '%s' already exists", self.collection)

    def upsert(
        self,
        text: str,                  # child chunk text (what was embedded)
        embedding: list[float],     # vector from sentence-transformers
        parent_text: str,           # full parent chunk (returned to LLM for context)
        metadata: dict[str, Any],   # source, project, type, author, etc.
        chunk_id: str = None,       # optional — auto-generated if not provided
    ) -> str:
        """
        Store one chunk in Qdrant.
        Returns the chunk_id (UUID string).

        Payload structure (what's stored alongside the vector):
            text          — child chunk text
            parent_text   — full parent chunk (fetched at retrieval time)
            source        — jira | github | slack | drive | local
            project       — e.g. "antlog"
            type          — doc | adr | chat | ticket | code
            stale         — false (set true when doc is updated)
            + any other metadata fields passed in
        """
        chunk_id = chunk_id or str(uuid.uuid4())

        # Build the full payload stored in Qdrant
        payload = {
            "text": text,
            "parent_text": parent_text,
            "stale": False,
            **metadata,
        }

        point = PointStruct(
            id=chunk_id,
            vector=embedding,
            payload=payload,
        )

        self.client.upsert(
            collection_name=self.collection,
            points=[point],
        )
        logger.debug("VectorStore: upserted chunk %s (source=%s)", chunk_id, metadata.get("source"))
        return chunk_id

    def search(
        self,
        query_embedding: list[float],
        project: str,
        doc_types: list[str] = None,    # filter by type if specified
        top_k: int = 30,                # return more than needed — reranker will trim
    ) -> list[dict]:
        """
        Semantic vector search with metadata pre-filter.

        Pre-filter is applied BEFORE vector search (Qdrant does this efficiently).
        This eliminates irrelevant projects from the search space entirely.

        Returns list of dicts with keys: id, score, text, parent_text, + metadata
        """
        # Build Qdrant filter — always filter by project and stale=false
        must_conditions = [
            FieldCondition(key="project", match=MatchValue(value=project)),
            FieldCondition(key="stale",   match=MatchValue(value=False)),
        ]

        # Optional: filter by document type(s)
        if doc_types:
            must_conditions.append(
                FieldCondition(key="type", match=MatchValue(value=doc_types[0]))
                # Note: for multiple types, Qdrant needs Filter.should — simplified here
            )

        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_embedding,
            query_filter=Filter(must=must_conditions),
            limit=top_k,
            with_payload=True,
        )

        return [
            {
                "id":          str(r.id),
                "score":       r.score,             # cosine similarity 0.0–1.0
                "text":        r.payload.get("text", ""),
                "parent_text": r.payload.get("parent_text", ""),
                "source":      r.payload.get("source", "unknown"),
                "type":        r.payload.get("type", "unknown"),
                "metadata":    r.payload,
            }
            for r in results
        ]

    def mark_stale(self, project: str, source: str):
        """
        Mark all chunks for a given project+source as stale.
        Called before re-ingesting a document so old chunks don't pollute search.
        """
        self.client.set_payload(
            collection_name=self.collection,
            payload={"stale": True},
            points=Filter(
                must=[
                    FieldCondition(key="project", match=MatchValue(value=project)),
                    FieldCondition(key="source",  match=MatchValue(value=source)),
                ]
            ),
        )
        logger.info("VectorStore: marked chunks stale for project=%s source=%s", project, source)

    def count(self) -> int:
        """Returns total number of vectors in the collection."""
        info = self.client.get_collection(self.collection)
        return info.points_count or 0
