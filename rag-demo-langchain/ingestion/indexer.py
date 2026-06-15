import logging
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from core.interfaces import BaseVectorStore
from core.models import Chunk, RetrievedChunk
from core.exceptions import StoreError, SearchError
from config.settings import Settings

logger = logging.getLogger(__name__)


def _to_uuid(chunk_id: str) -> str:
    """Convert an arbitrary string chunk ID to a stable UUID string."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))


class QdrantVectorStore(BaseVectorStore):
    """Step 4 / Step 6 — Persists and searches vectors using Qdrant (local, no server).

    Embeddings are computed externally (Steps 3 & 5) and passed in,
    keeping embed and store as visually separate pipeline steps.
    Cosine scores returned by Qdrant are already similarity [0, 1].
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._name = settings.qdrant_collection
        self._client = QdrantClient(path=settings.qdrant_db_path)
        if not self._client.collection_exists(self._name):
            self._client.create_collection(
                collection_name=self._name,
                vectors_config=VectorParams(
                    size=settings.vector_size,
                    distance=Distance.COSINE,
                ),
            )

    # ── Step 4 ────────────────────────────────────────────────────────────────

    def store(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        try:
            self._client.upsert(
                collection_name=self._name,
                points=[
                    PointStruct(
                        id=_to_uuid(c.id),
                        vector=emb,
                        payload={
                            "chunk_id": c.id,
                            "text": c.text,
                            "source": c.source,
                            "chunk_index": c.chunk_index,
                        },
                    )
                    for c, emb in zip(chunks, embeddings)
                ],
            )
            logger.info("Stored %d chunks in Qdrant", len(chunks))
        except Exception as exc:
            raise StoreError("Failed to store chunks in Qdrant") from exc

    # ── Step 6 ────────────────────────────────────────────────────────────────

    def search(self, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        try:
            # qdrant-client >= 1.7: query_points() replaces the removed search()
            response = self._client.query_points(
                collection_name=self._name,
                query=query_embedding,
                limit=top_k,
                with_payload=True,
                with_vectors=True,
            )
            return [
                RetrievedChunk(
                    id=r.payload["chunk_id"],
                    text=r.payload["text"],
                    source=r.payload["source"],
                    chunk_index=int(r.payload["chunk_index"]),
                    score=float(r.score),
                    rank=rank,
                    embedding=list(r.vector) if r.vector else [],
                )
                for rank, r in enumerate(response.points, start=1)
            ]
        except Exception as exc:
            raise SearchError("Failed to search Qdrant") from exc

    # ── Utility ───────────────────────────────────────────────────────────────

    def is_indexed(self) -> bool:
        return (
            self._client.collection_exists(self._name)
            and (self._client.get_collection(self._name).points_count or 0) > 0
        )

    def count(self) -> int:
        if not self._client.collection_exists(self._name):
            return 0
        return self._client.get_collection(self._name).points_count or 0

    def clear(self) -> None:
        self._client.delete_collection(self._name)
        self._client.create_collection(
            collection_name=self._name,
            vectors_config=VectorParams(
                size=self._settings.vector_size,
                distance=Distance.COSINE,
            ),
        )
        logger.info("Qdrant collection cleared")

    def get_all(self) -> dict[str, list[dict]]:
        points, _ = self._client.scroll(
            collection_name=self._name,
            limit=10000,
            with_payload=True,
        )
        grouped: dict[str, list] = {}
        for p in points:
            src = p.payload["source"]
            grouped.setdefault(src, []).append({
                "chunk_index": int(p.payload["chunk_index"]),
                "text": p.payload["text"],
            })
        for src in grouped:
            grouped[src].sort(key=lambda x: x["chunk_index"])
        return grouped
