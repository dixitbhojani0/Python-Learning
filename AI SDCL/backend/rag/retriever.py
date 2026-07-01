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
import re
from dataclasses import dataclass
from sentence_transformers import SentenceTransformer, CrossEncoder
from rank_bm25 import BM25Okapi

try:
    from langsmith import traceable as _traceable
except ImportError:
    def _traceable(fn=None, **_kw):
        return fn if fn is not None else (lambda f: f)

from backend.core.config_loader import config
from backend.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Filler words stripped before matching a recall query against document titles,
# so "show me the clean code checklist" matches on {clean, code, checklist}.
_RECALL_STOPWORDS = {
    "show", "me", "the", "a", "an", "of", "for", "my", "our", "please", "give",
    "whole", "entire", "full", "complete", "all", "list", "to", "and", "what",
    "is", "are", "in", "on", "see", "view", "get", "tell", "about",
}

# ── Load models once at module import time (expensive — do NOT load per-request)
# SentenceTransformer auto-downloads on first run (~90MB, cached in ~/.cache/)
_EMBED_MODEL = None
_RERANK_MODEL = None


def _get_embed_model() -> SentenceTransformer:
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        rag_cfg    = config.get_rag_config()
        model_name = rag_cfg.get("embeddings", {}).get("model", "all-MiniLM-L6-v2")
        config_dim = rag_cfg.get("embeddings", {}).get("dimension", 384)
        logger.info("HybridRetriever: loading embedding model '%s'...", model_name)
        model = SentenceTransformer(model_name)
        actual_dim = model.get_sentence_embedding_dimension()
        if actual_dim != config_dim:
            raise ValueError(
                f"Embedding dimension mismatch: rag_sources.yaml says {config_dim} but "
                f"'{model_name}' produces {actual_dim}-dim vectors. "
                f"Update embeddings.dimension in config/rag_sources.yaml."
            )
        _EMBED_MODEL = model
        logger.info("HybridRetriever: embedding model loaded (dim=%d)", actual_dim)
    return _EMBED_MODEL


def _get_rerank_model() -> CrossEncoder:
    global _RERANK_MODEL
    if _RERANK_MODEL is None:
        model_name = config.get_rag_config().get("embeddings", {}).get(
            "reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        logger.info("HybridRetriever: loading reranker model '%s'...", model_name)
        _RERANK_MODEL = CrossEncoder(model_name)
        logger.info("HybridRetriever: reranker model loaded")
    return _RERANK_MODEL


def rerank_relevance(query: str, texts: list[str]) -> float:
    """
    Return the MAX reranker relevance (0.0–1.0) of `query` against candidate `texts`.

    Used by agents to judge whether live MCP data is actually on-topic — an ML
    relevance signal (the same CrossEncoder used for RAG), not keyword presence.
    Returns 0.0 for no candidates. Fails OPEN (1.0) on model error so a reranker
    failure never suppresses legitimate live data.
    """
    candidates = [t for t in texts if t and t.strip()]
    if not candidates:
        return 0.0
    try:
        model  = _get_rerank_model()
        scores = model.predict([(query, t) for t in candidates])
        return max(_sigmoid(float(s)) for s in scores)
    except Exception:
        logger.exception("rerank_relevance: scoring failed — treating as relevant (fail-open)")
        return 1.0


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
    embed_cfg = config.get_rag_config().get("embeddings", {})
    normalize = embed_cfg.get("normalize", True)
    batch_size = embed_cfg.get("batch_size", 32)
    vectors = model.encode(
        texts,
        normalize_embeddings=normalize,
        batch_size=batch_size,
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
    """
    Map CrossEncoder ms-marco raw logit → display confidence [0, 1].

    Temperature T=3 spreads the typical domain-specific logit range [-5, +5]
    across a meaningful display range:
        logit -5  → 0.19  (19% — weak evidence)
        logit  0  → 0.50  (50% — neutral)
        logit +5  → 0.81  (81% — strong evidence)

    Without temperature (T=1), logit -5 → 0.007 = 1% which looks broken
    even when the retrieval is actually finding relevant chunks.
    The ms-marco model was trained on web queries (not sprint docs) so its
    raw logits are systematically lower for technical domain text.
    """
    T = config.get_rag_config().get("retrieval", {}).get("sigmoid_temperature", 3.0)
    return 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, x / T))))


def _reciprocal_rank_fusion(
    list_a: list[dict],
    list_b: list[dict],
    k: int | None = None,
) -> list[dict]:
    """
    Reciprocal Rank Fusion over two INDEPENDENT ranked lists.

    Formula: RRF(d) = Σ 1 / (k + rank(d))
    k=60 is the standard default (Cormack et al., 2009).

    Accepts two separate ranked lists (vector results and BM25 results) — each
    produced independently from the full corpus. Documents appearing in both lists
    score higher than those in only one.
    """
    if k is None:
        k = config.get_rag_config().get("retrieval", {}).get("rrf_k", 60)
    rrf_scores: dict[str, float] = {}
    id_to_doc: dict[str, dict] = {}

    for rank, doc in enumerate(list_a):
        doc_id = doc["id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        id_to_doc[doc_id] = doc

    for rank, doc in enumerate(list_b):
        doc_id = doc["id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        if doc_id not in id_to_doc:
            id_to_doc[doc_id] = doc

    sorted_ids = sorted(rrf_scores, key=lambda d: rrf_scores[d], reverse=True)
    return [id_to_doc[doc_id] for doc_id in sorted_ids]


class HybridRetriever:
    """
    Main retrieval class used by all agents.

    Usage:
        retriever = HybridRetriever()
        chunks, confidence = retriever.retrieve("CORS error nginx auth", project="SDLC")
        # chunks: list[RetrievedChunk], confidence: float (reranker top score)
    """

    def __init__(self):
        self.vector_store = VectorStore()
        rag_cfg = config.get_rag_config().get("retrieval", {})
        self.initial_candidates = rag_cfg.get("initial_candidates", 30)
        self.top_k = rag_cfg.get("top_k_after_rerank", 7)
        self.confidence_thresholds = config.get_confidence_thresholds()
        # Per-project BM25 index cache: project → (BM25Okapi, list[dict])
        # Built lazily on first retrieve(); call clear_bm25_cache() after re-ingest.
        self._bm25_cache: dict[str, tuple] = {}

    def _get_or_build_bm25(self, project: str) -> tuple:
        """
        Return (BM25Okapi model, corpus_docs list) for this project.

        Builds from full Qdrant corpus on first call, then caches in memory.
        Full-corpus BM25 ensures keyword matches outside the vector top-N are
        not invisible — the standard hybrid retrieval pattern.
        """
        if project not in self._bm25_cache:
            records = self.vector_store.scroll_chunks(project, with_vectors=False)
            corpus_docs = [
                {
                    "id":          str(r.id),
                    "text":        r.payload.get("text", ""),
                    "parent_text": r.payload.get("parent_text", ""),
                    "source":      r.payload.get("source", "unknown"),
                    "type":        r.payload.get("type", "unknown"),
                    "metadata":    r.payload,
                }
                for r in records
                if r.payload.get("text")
            ]
            tokenized = [doc["text"].lower().split() for doc in corpus_docs]
            bm25 = BM25Okapi(tokenized) if tokenized else BM25Okapi([[]])
            self._bm25_cache[project] = (bm25, corpus_docs)
            logger.info(
                "HybridRetriever: built BM25 index project='%s' — %d docs",
                project, len(corpus_docs),
            )
        return self._bm25_cache[project]

    def clear_bm25_cache(self, project: str = None) -> None:
        """Invalidate BM25 index cache. Call after re-ingest. None = clear all projects."""
        if project:
            self._bm25_cache.pop(project, None)
        else:
            self._bm25_cache.clear()
        logger.info("HybridRetriever: BM25 cache cleared (project=%s)", project or "all")

    @_traceable(name="hybrid_retriever", run_type="retriever")
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

        # ── Step 2: Independent BM25 search on FULL corpus
        # BM25 runs over all non-parent chunks for this project (not just the
        # vector top-N) so keyword-only matches are never invisible.
        bm25_model, bm25_corpus = self._get_or_build_bm25(project)
        bm25_raw = bm25_model.get_scores(query.lower().split())
        top_bm25_idx = sorted(
            range(len(bm25_raw)), key=lambda i: bm25_raw[i], reverse=True
        )[:self.initial_candidates]
        bm25_results = [bm25_corpus[i] for i in top_bm25_idx if bm25_raw[i] > 0]

        # ── Step 3: RRF fuse two independent ranked lists
        fused = _reciprocal_rank_fusion(vector_results, bm25_results)

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

    async def retrieve_with_corrective_rag(
        self,
        query: str,
        project: str,
        llm_rewrite_fn,             # async callable: async (query: str) -> str
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
        reformulated = await llm_rewrite_fn(query)
        logger.info("HybridRetriever: reformulated query: '%s'", reformulated[:100])

        chunks2, confidence2 = self.retrieve(reformulated, project)

        if confidence2 >= low_threshold:
            logger.info(
                "HybridRetriever: corrective RAG RECOVERED — confidence %.3f → %.3f (strategy='corrective')",
                confidence, confidence2,
            )
            return chunks2, confidence2, "corrective"

        # Still low after retry — return best of both attempts
        best_chunks = chunks if confidence >= confidence2 else chunks2
        best_confidence = max(confidence, confidence2)
        logger.info(
            "HybridRetriever: corrective RAG could not recover — confidence %.3f / %.3f (strategy='degraded')",
            confidence, confidence2,
        )
        return best_chunks, best_confidence, "degraded"

    def retrieve_full_document(self, doc_title: str, project: str) -> list[RetrievedChunk]:
        """
        RECALL mode: return ALL chunks of a document, in authored order.

        For "show me the whole/entire/complete X" queries where the user wants the
        full section reassembled — not the top-k most similar fragments. Pairs with
        identify_document() which picks the doc_title from a cheap semantic probe.

        Returns RetrievedChunk objects ordered by chunk_index (images last). Score is
        set to 1.0 — these are returned because the document was explicitly requested,
        not ranked by similarity.
        """
        rows = self.vector_store.get_document_chunks(doc_title, project)
        chunks = [
            RetrievedChunk(
                text=r["text"],
                parent_text=r["metadata"].get("parent_text") or r["text"],
                source=r["metadata"].get("source", "unknown"),
                doc_type=r["metadata"].get("type", "unknown"),
                score=1.0,
                metadata=r["metadata"],
            )
            for r in rows
        ]
        logger.info(
            "HybridRetriever.retrieve_full_document: '%s' → %d chunks (recall mode)",
            doc_title, len(chunks),
        )
        return chunks

    def identify_document(self, query: str, project: str) -> str:
        """
        Find the most likely target doc_title for a recall query.

        A pure "top semantic chunk" probe is biased toward large, image-heavy docs:
        e.g. "clean code checklist" would land on the 114-chunk "Clean Code Best
        Practices" deck instead of the 12-chunk "Clean Code Checklist". So among the
        candidate docs the probe surfaces, prefer the one whose TITLE best matches the
        query words; fall back to the top chunk's doc when no title overlaps.
        Returns "" if nothing confident is found (caller falls back to normal retrieval).
        """
        chunks, confidence = self.retrieve(query, project)
        if not chunks:
            return ""
        no_evidence = self.confidence_thresholds.get("no_evidence_threshold", 0.20)
        if confidence < no_evidence:
            return ""

        q_words = set(re.findall(r"[a-z0-9]+", query.lower())) - _RECALL_STOPWORDS
        # Candidate titles in semantic-rank order (de-duplicated)
        candidates: list[str] = []
        for c in chunks:
            t = (c.metadata or {}).get("doc_title", "")
            if t and t not in candidates:
                candidates.append(t)

        def title_overlap(title: str) -> int:
            return len(q_words & set(re.findall(r"[a-z0-9]+", title.lower())))

        # Highest title-overlap wins; ties broken by best semantic rank (earliest).
        best = max(candidates, key=lambda t: (title_overlap(t), -candidates.index(t)))
        if title_overlap(best) >= 1:
            return best
        return chunks[0].metadata.get("doc_title", "") or ""

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
