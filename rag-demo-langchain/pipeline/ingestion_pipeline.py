import logging
from core.interfaces import BaseLoader, BaseChunker, BaseEmbedder, BaseVectorStore
from core.models import IngestionResult, DocStats

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Orchestrates the 4-step ingestion flow: Load → Chunk → Embed → Store.

    Each step is a named dependency injected at construction time,
    making it trivial to swap any step without touching the others.
    """

    def __init__(
        self,
        loader: BaseLoader,
        chunker: BaseChunker,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
    ) -> None:
        self._loader = loader
        self._chunker = chunker
        self._embedder = embedder
        self._vector_store = vector_store

    def run(self, docs_dir: str, force: bool = False) -> IngestionResult:
        if self._vector_store.is_indexed() and not force:
            logger.info("Already indexed — skipping ingestion (use force=True to re-index)")
            return IngestionResult(total_files=0, total_chunks=0, doc_stats=[])

        if force:
            self._vector_store.clear()

        # ── Step 1: Load ──────────────────────────────────────────────────────
        logger.info("Step 1 — Loading documents from: %s", docs_dir)
        raw_docs, _ = self._loader.load(docs_dir)

        # ── Step 2: Chunk ─────────────────────────────────────────────────────
        logger.info("Step 2 — Chunking %d documents", len(raw_docs))
        chunks = self._chunker.chunk(raw_docs)

        # ── Step 3: Embed ─────────────────────────────────────────────────────
        logger.info("Step 3 — Embedding %d chunks", len(chunks))
        embeddings = self._embedder.embed_documents([c.text for c in chunks])

        # ── Step 4: Store ─────────────────────────────────────────────────────
        logger.info("Step 4 — Storing in ChromaDB")
        self._vector_store.store(chunks, embeddings)

        # Build per-file stats
        chunk_counts: dict[str, int] = {}
        char_counts: dict[str, int] = {}
        for chunk in chunks:
            chunk_counts[chunk.source] = chunk_counts.get(chunk.source, 0) + 1
            char_counts[chunk.source] = char_counts.get(chunk.source, 0) + len(chunk.text)

        doc_stats = [
            DocStats(file=src, chunks=count, chars=char_counts[src])
            for src, count in sorted(chunk_counts.items())
        ]

        logger.info("Ingestion complete — %d chunks from %d files", len(chunks), len(doc_stats))
        return IngestionResult(
            total_files=len(doc_stats),
            total_chunks=len(chunks),
            doc_stats=doc_stats,
        )
