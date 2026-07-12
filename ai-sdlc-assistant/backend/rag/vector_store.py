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
    MatchAny,
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
            project       — e.g. "SDLC"
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

    def upsert_parent(
        self,
        parent_id:   str,
        parent_text: str,
        embedding:   list[float],
        base_metadata: dict,
    ) -> None:
        """
        Store a parent chunk as a separate Qdrant point tagged is_parent=True.

        Parents are excluded from normal semantic searches (search() filters them out)
        but are retrievable by ID via get_by_parent_id() for two-stage lookups:
          1. search() → child match → parent_id
          2. get_by_parent_id() → all sibling chunks + full parent text

        The parent embedding enables optional "search parents directly" queries
        but they won't appear in standard search() calls.
        """
        payload = {
            "text":        parent_text,
            "parent_text": parent_text,
            "is_parent":   True,
            "stale":       False,
            **base_metadata,
        }
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=parent_id, vector=embedding, payload=payload)],
        )
        logger.debug("VectorStore: upserted parent %s", parent_id)

    def get_by_parent_id(self, parent_id: str, project: str) -> list[dict]:
        """
        Return all child chunks that share a parent_id.

        Use case: after matching child chunk A, call get_by_parent_id(A.parent_id)
        to retrieve all sibling chunks — gives the full section, not just the matched fragment.
        """
        records, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="parent_id", match=MatchValue(value=parent_id)),
                    FieldCondition(key="project",   match=MatchValue(value=project)),
                ],
                must_not=[
                    FieldCondition(key="is_parent", match=MatchValue(value=True)),
                ],
            ),
            limit=50,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {"id": str(r.id), "text": r.payload.get("text", ""), "metadata": r.payload}
            for r in records
        ]

    def get_document_chunks(self, doc_title: str, project: str) -> list[dict]:
        """
        Return ALL non-stale child chunks for a document, ordered by chunk_index.

        This is the document/section RECALL path — for "show me the whole checklist"
        type queries where the user wants the complete document reassembled, not the
        top-k most similar fragments. Uses the doc_title + chunk_index metadata that
        is already stored at ingestion; no vector search involved.

        Image chunks (is_image=True) are included so the caller can surface the
        document's images alongside the reassembled text.
        """
        records: list = []
        offset = None
        while True:
            page, next_offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="doc_title", match=MatchValue(value=doc_title)),
                        FieldCondition(key="project",   match=MatchValue(value=project)),
                        FieldCondition(key="stale",     match=MatchValue(value=False)),
                    ],
                    must_not=[
                        FieldCondition(key="is_parent", match=MatchValue(value=True)),
                    ],
                ),
                offset=offset,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
            records.extend(page)
            if next_offset is None:
                break
            offset = next_offset

        # Order by chunk_index so the section reads top-to-bottom as authored.
        # Image chunks may lack chunk_index — sort them last, stable by page.
        def _order(r) -> tuple:
            md = r.payload or {}
            ci = md.get("chunk_index")
            if ci is None:
                return (1, md.get("page_number") or 0)
            return (0, ci)

        records.sort(key=_order)
        return [
            {"id": str(r.id), "text": r.payload.get("text", ""), "metadata": r.payload}
            for r in records
        ]

    def update_payload(self, chunk_id: str, updates: dict) -> None:
        """
        Update specific payload fields of an existing chunk without changing its vector.
        Used by build_cross_document_links() to write related_chunk_ids after ingest.
        """
        self.client.set_payload(
            collection_name=self.collection,
            payload=updates,
            points=[chunk_id],
        )

    def search_related(
        self,
        query_embedding: list[float],
        exclude_source:  str,
        project:         str,
        top_k:           int   = 3,
        min_score:       float = 0.75,
    ) -> list[dict]:
        """
        Find similar chunks from a DIFFERENT source for cross-document linking.

        Excludes the chunk's own source so links always point to external documents.
        Used by build_cross_document_links() after all documents are ingested.
        """
        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_embedding,
            query_filter=Filter(
                must=[
                    FieldCondition(key="project", match=MatchValue(value=project)),
                    FieldCondition(key="stale",   match=MatchValue(value=False)),
                ],
                must_not=[
                    FieldCondition(key="source",    match=MatchValue(value=exclude_source)),
                    FieldCondition(key="is_parent", match=MatchValue(value=True)),
                ],
            ),
            limit=top_k,
            score_threshold=min_score,
            with_payload=True,
        )
        return [
            {
                "id":     str(r.id),
                "score":  r.score,
                "text":   r.payload.get("text", ""),
                "source": r.payload.get("source", ""),
            }
            for r in results
        ]

    def scroll_chunks(self, project: str, with_vectors: bool = False) -> list:
        """
        Iterate all non-stale, non-parent chunks for a project.
        Used by build_cross_document_links() which needs every chunk's vector.
        Returns list of qdrant Record objects (access .id, .payload, .vector).
        """
        all_records = []
        offset = None
        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="project", match=MatchValue(value=project)),
                        FieldCondition(key="stale",   match=MatchValue(value=False)),
                    ],
                    must_not=[
                        FieldCondition(key="is_parent", match=MatchValue(value=True)),
                    ],
                ),
                offset=offset,
                limit=100,
                with_payload=True,
                with_vectors=with_vectors,
            )
            all_records.extend(records)
            if next_offset is None:
                break
            offset = next_offset
        return all_records

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
        Parent points (is_parent=True) are excluded — they are retrieved separately
        via get_by_parent_id() when needed, not ranked in search results.

        Returns list of dicts with keys: id, score, text, parent_text, + metadata
        """
        must_conditions = [
            FieldCondition(key="project", match=MatchValue(value=project)),
            FieldCondition(key="stale",   match=MatchValue(value=False)),
        ]
        if doc_types:
            if len(doc_types) == 1:
                must_conditions.append(
                    FieldCondition(key="type", match=MatchValue(value=doc_types[0]))
                )
            else:
                must_conditions.append(
                    FieldCondition(key="type", match=MatchAny(any=doc_types))
                )

        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_embedding,
            query_filter=Filter(
                must=must_conditions,
                # Exclude parent-only points — they are indexed but not searched directly
                must_not=[FieldCondition(key="is_parent", match=MatchValue(value=True))],
            ),
            limit=top_k,
            with_payload=True,
        )

        return [
            {
                "id":              str(r.id),
                "score":           r.score,
                "text":            r.payload.get("text", ""),
                "parent_text":     r.payload.get("parent_text", ""),
                "source":          r.payload.get("source", "unknown"),
                "type":            r.payload.get("type", "unknown"),
                "next_chunk_id":   r.payload.get("next_chunk_id", ""),
                "prev_chunk_id":   r.payload.get("prev_chunk_id", ""),
                "related_chunk_ids": r.payload.get("related_chunk_ids", []),
                "metadata":        r.payload,
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
        """Returns number of child chunks (excludes parent-only points)."""
        try:
            result = self.client.count(
                collection_name=self.collection,
                count_filter=Filter(
                    must_not=[FieldCondition(key="is_parent", match=MatchValue(value=True))]
                ),
            )
            return result.count
        except Exception:
            info = self.client.get_collection(self.collection)
            return info.points_count or 0
