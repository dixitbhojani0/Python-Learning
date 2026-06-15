import chromadb
from chromadb.config import Settings
from models import Chunk, RetrievedChunk
from config import CHROMA_DB_PATH, CHROMA_COLLECTION


class VectorStore:
    def __init__(self) -> None:
        self._client = chromadb.PersistentClient(
            path=CHROMA_DB_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        self._collection.add(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[{"source": c.source, "chunk_index": c.chunk_index} for c in chunks],
        )

    def query(self, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances", "embeddings"],
        )
        retrieved: list[RetrievedChunk] = []
        for rank, (doc_id, text, meta, distance, embedding) in enumerate(zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
            results["embeddings"][0],
        ), start=1):
            # ChromaDB cosine distance: 0 = identical, 2 = opposite → convert to similarity
            score = max(0.0, 1.0 - distance)
            retrieved.append(RetrievedChunk(
                id=doc_id,
                text=text,
                source=meta["source"],
                chunk_index=int(meta["chunk_index"]),
                score=score,
                rank=rank,
                embedding=list(embedding),
            ))
        return retrieved

    def get_all(self) -> dict[str, list[dict]]:
        results = self._collection.get(include=["documents", "metadatas"])
        grouped: dict[str, list] = {}
        for doc, meta in zip(results["documents"], results["metadatas"]):
            src = meta["source"]
            grouped.setdefault(src, []).append({
                "chunk_index": int(meta["chunk_index"]),
                "text": doc,
            })
        for src in grouped:
            grouped[src].sort(key=lambda x: x["chunk_index"])
        return grouped

    def is_indexed(self) -> bool:
        return self._collection.count() > 0

    def count(self) -> int:
        return self._collection.count()

    def clear(self) -> None:
        self._client.delete_collection(CHROMA_COLLECTION)
        self._collection = self._client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
