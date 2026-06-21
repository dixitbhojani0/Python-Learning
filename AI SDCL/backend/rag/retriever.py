"""
backend/rag/retriever.py

Hybrid retrieval pipeline: BM25 (sparse) + Vector (dense) + Reranker.

Why hybrid search?
  BM25 excels at exact keyword matches  → finds "SDLC-1042", "/api/v2/auth", "nginx"
  Vector search excels at semantic match → finds "connection pool issue" even if
                                           the user said "DB keeps crashing"
  Together they cover what neither can alone.

Pipeline (Step 8 in solution doc):
  1. Metadata pre-filter (Qdrant)   — removes irrelevant projects before searching
  2. Vector search                  — top 30 semantically similar chunks
  3. BM25 sparse search             — top 30 keyword matching chunks
  4. Reciprocal Rank Fusion         — intelligently merges both ranked lists
  5. Cross-encoder reranker         — scores all 30 for TRUE relevance to query
  6. Top 7 by reranker score        — only highest-quality chunks passed forward
  7. Parent chunk fetch             — return full parent text (not just the small child)

Official docs:
  sentence-transformers: https://www.sbert.net/docs/cross_encoder/usage/usage.html
  rank_bm25: https://github.com/dorianbrown/rank_bm25
"""
import logging
import math
from dataclasses import dataclass
from sentence_transformers import SentenceTransformer, CrossEncoder
from rank_bm25 import BM25Okapi

from backend.core.config_loader import config
from backend.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Load models once at module import time (expensive — do NOT load per-request)
# SentenceTransformer auto-downloads on first run (~90MB, cached in ~/.cache/)
_EMBED_MODEL = None
_RERANK_MODEL = None


def _get_embed_model() -> SentenceTransformer:
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        model_name = config.get_rag_config().get("embeddings", {}).get("model", "all-MiniLM-L6-v2")
        logger.info("HybridRetriever: loading embedding model '%s'...", model_name)
        _EMBED_MODEL = SentenceTransformer(model_name)
        logger.info("HybridRetriever: embedding model loaded")
    return _EMBED_MODEL


def _get_rerank_model() -> CrossEncoder:
    global _RERANK_MODEL
    if _RERANK_MODEL is None:
        # cross-encoder/ms-marco-MiniLM-L-6-v2 — free, local, production-grade
        # Same concept as bge-reranker-large but smaller and faster
        logger.info("HybridRetriever: loading reranker model...")
        _RERANK_MODEL = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        logger.info("HybridRetriever: reranker model loaded")
    return _RERANK_MODEL


def embed_text(text: str) -> list[float]:
    """
    Convert text to embedding vector using sentence-transformers.
    This is the same function used both at ingestion time and query time.
    Consistent embedding model = consistent similarity scores.
    """
    model = _get_embed_model()
    normalize = config.get_rag_config().get("embeddings", {}).get("normalize", True)
    vector = model.encode(text, normalize_embeddings=normalize)
    return vector.tolist()


def embed_texts_batch(texts: list[str]) -> list[list[float]]:
    """
    Batch encode multiple texts in one model.encode() call — ~5x faster than
    calling embed_text() in a loop during ingestion.

    Use this in RAGPipeline (bulk ingestion).
    Use embed_text() for single query encoding at retrieval time.
    """
    model = _get_embed_model()
    normalize = config.get_rag_config().get("embeddings", {}).get("normalize", True)
    vectors = model.encode(
        texts,
        normalize_embeddings=normalize,
        batch_size=32,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


@dataclass
class RetrievedChunk:
    """One retrieved chunk with its score and metadata."""
    text: str               # child chunk text
    parent_text: str        # full parent — what the LLM actually reads
    source: str             # jira | slack | github | local
    doc_type: str           # doc | adr | chat | ticket
    score: float            # reranker score (0.0–1.0 approx)
    metadata: dict          # all Qdrant payload fields


def _sigmoid(x: float) -> float:
    """Convert CrossEncoder raw logit to [0, 1] probability."""
    return 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, x))))


def _reciprocal_rank_fusion(
    vector_results: list[dict],
    bm25_scores: list[float],
    k: int = 60,
) -> list[dict]:
    """
    Reciprocal Rank Fusion (RRF) merges two ranked lists intelligently.

    Formula: RRF(d) = Σ 1 / (k + rank(d))
    A document ranked #1 in both lists scores higher than one ranked #1 in only one list.
    k=60 is the standard value from the original RRF paper (Cormack et al., 2009).

    Why not just average scores?
    - Vector scores and BM25 scores are on different scales
    - RRF uses ranks (not raw scores) — scale-independent and more robust
    """
    rrf_scores: dict[str, float] = {}
    id_to_doc: dict[str, dict] = {}

    # Score from vector search (already ranked by Qdrant)
    for rank, doc in enumerate(vector_results):
        doc_id = doc["id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        id_to_doc[doc_id] = doc

    # Score from BM25 (re-rank the same docs by BM25 score)
    bm25_ranked = sorted(
        zip(range(len(vector_results)), bm25_scores),
        key=lambda x: x[1],
        reverse=True,
    )
    for rank, (doc_idx, _) in enumerate(bm25_ranked):
        doc_id = vector_results[doc_idx]["id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)

    # Return docs sorted by combined RRF score
    sorted_ids = sorted(rrf_scores, key=lambda d: rrf_scores[d], reverse=True)
    return [id_to_doc[doc_id] for doc_id in sorted_ids if doc_id in id_to_doc]


class HybridRetriever:
    """
    Main retrieval class used by all agents.

    Usage:
        retriever = HybridRetriever()
        chunks, confidence = retriever.retrieve("CORS error nginx auth", project="antlog")
        # chunks: list[RetrievedChunk], confidence: float (reranker top score)
    """

    def __init__(self):
        self.vector_store = VectorStore()
        rag_cfg = config.get_rag_config().get("retrieval", {})
        self.initial_candidates = rag_cfg.get("initial_candidates", 30)
        self.top_k = rag_cfg.get("top_k_after_rerank", 7)
        self.confidence_thresholds = config.get_confidence_thresholds()

    def retrieve(
        self,
        query: str,
        project: str,
        doc_types: list[str] = None,
    ) -> tuple[list[RetrievedChunk], float]:
        """
        Full hybrid retrieval pipeline.

        Returns:
            (chunks, confidence_score)
            chunks: top-k RetrievedChunk objects, sorted by reranker score desc
            confidence_score: reranker score of the top chunk (0.0–1.0)

        The confidence_score is used by the agent to decide:
            ≥ 0.75 → answer confidently
            0.45–0.74 → answer with caveat
            < 0.45 → trigger corrective RAG (reformulate + retry)
        """
        if not query or not query.strip():
            return [], 0.0

        logger.info("HybridRetriever: retrieving for query='%s' project='%s'", query[:80], project)

        # ── Step 1: Vector search (dense semantic)
        query_embedding = embed_text(query)
        vector_results = self.vector_store.search(
            query_embedding=query_embedding,
            project=project,
            doc_types=doc_types,
            top_k=self.initial_candidates,
        )

        if not vector_results:
            logger.warning("HybridRetriever: no vector results for project='%s'", project)
            return [], 0.0

        # ── Step 2: BM25 sparse search (exact keyword matching)
        corpus = [r["text"] for r in vector_results]
        tokenized_corpus = [doc.lower().split() for doc in corpus]
        bm25 = BM25Okapi(tokenized_corpus)
        bm25_scores = bm25.get_scores(query.lower().split())

        # ── Step 3: Reciprocal Rank Fusion — merge vector + BM25 rankings
        fused = _reciprocal_rank_fusion(vector_results, bm25_scores)

        # ── Step 4: Cross-encoder reranker — true relevance scoring
        reranker = _get_rerank_model()
        pairs = [(query, doc["text"]) for doc in fused]
        rerank_scores = reranker.predict(pairs)

        # ── Step 5: Sort by reranker score, take top-k
        scored = sorted(
            zip(fused, rerank_scores),
            key=lambda x: x[1],
            reverse=True,
        )
        top_results = scored[:self.top_k]

        # ── Step 6: Build RetrievedChunk objects (use parent_text for LLM context)
        chunks = [
            RetrievedChunk(
                text=doc["text"],
                parent_text=doc.get("parent_text") or doc["text"],
                source=doc.get("source", "unknown"),
                doc_type=doc.get("type", "unknown"),
                score=_sigmoid(float(score)),
                metadata=doc.get("metadata", {}),
            )
            for doc, score in top_results
        ]

        top_score = chunks[0].score if chunks else 0.0
        logger.info(
            "HybridRetriever: returning %d chunks, top score=%.3f, confidence=%s",
            len(chunks), top_score, self._confidence_tier(top_score)
        )

        return chunks, top_score

    def retrieve_with_corrective_rag(
        self,
        query: str,
        project: str,
        llm_rewrite_fn,             # callable: (query: str) -> str
    ) -> tuple[list[RetrievedChunk], float, str]:
        """
        Corrective RAG (Adaptive RAG loop from solution doc Section 4.8).

        If first retrieval confidence is low (<0.45), reformulates the query
        via LLM and tries again before falling back to graceful degradation.

        Returns: (chunks, confidence, strategy_used)
          strategy_used: "first_pass" | "corrective" | "degraded"
        """
        low_threshold = self.confidence_thresholds.get("low_threshold", 0.45)

        # First retrieval attempt
        chunks, confidence = self.retrieve(query, project)

        if confidence >= low_threshold:
            return chunks, confidence, "first_pass"

        # Low confidence — try corrective RAG
        logger.info("HybridRetriever: low confidence (%.3f) — triggering corrective RAG", confidence)
        reformulated = llm_rewrite_fn(query)
        logger.info("HybridRetriever: reformulated query: '%s'", reformulated[:100])

        chunks2, confidence2 = self.retrieve(reformulated, project)

        if confidence2 >= low_threshold:
            return chunks2, confidence2, "corrective"

        # Still low after retry — return best of both attempts
        best_chunks = chunks if confidence >= confidence2 else chunks2
        best_confidence = max(confidence, confidence2)
        return best_chunks, best_confidence, "degraded"

    def _confidence_tier(self, score: float) -> str:
        """Human-readable confidence tier for logging."""
        high = self.confidence_thresholds.get("high_threshold", 0.75)
        medium = self.confidence_thresholds.get("medium_threshold", 0.45)
        no_evidence = self.confidence_thresholds.get("no_evidence_threshold", 0.20)
        if score >= high:
            return "HIGH"
        if score >= medium:
            return "MEDIUM"
        if score >= no_evidence:
            return "LOW"
        return "NO_EVIDENCE"
