"""
backend/core/metrics.py

Automated evaluation layer — three scoring dimensions per query:

  1. Retrieval Precision  — did the retriever surface the right chunks?
     Measures: what fraction of expected keywords appear in the top-K retrieved chunks.
     Range: 0.0 (none found) → 1.0 (all expected keywords found).

  2. Faithfulness  — does the response stay grounded in the retrieved evidence?
     Uses LLM-as-judge: the LLM reads the chunks + response and flags unsupported claims.
     Range: 0.0 (response contradicts evidence) → 1.0 (every claim is supported).

  3. Answer Relevancy  — is the response on-topic for the query?
     Measures: cosine similarity between embed(query) and embed(response).
     Range: 0.0 (totally off-topic) → 1.0 (perfect topical match).

Results are appended to data/eval_results.jsonl — one JSON line per query per run.
This gives a historical record so you can see if RAG changes improved or degraded quality.

Why three metrics and not just one?
  - Precision catches retrieval failures: right intent, wrong chunks.
  - Faithfulness catches hallucination: LLM adds facts not in the evidence.
  - Relevancy catches drift: LLM answers a different question than was asked.
  A system can score well on one and fail on another — you need all three.
"""
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RESULTS_PATH = Path(__file__).parents[2] / "data" / "eval_results.jsonl"


# ── Retrieval Precision ───────────────────────────────────────────────────────

def retrieval_precision(
    chunks: list[dict],
    expected_keywords: list[str],
) -> float:
    """
    Keyword coverage score: what fraction of expected_keywords appear in
    at least one retrieved chunk?

    A keyword is "found" if it appears (case-insensitive) in the chunk's
    `text` or `parent_text` field.

    Returns 0.0 if expected_keywords is empty (no ground truth to check against).
    """
    if not expected_keywords or not chunks:
        return 0.0

    # Build one big text blob from all retrieved chunks for fast substring search
    chunk_texts = " ".join(
        (c.get("parent_text") or c.get("text") or "")
        for c in chunks
    ).lower()

    matched = sum(
        1 for kw in expected_keywords
        if kw.lower() in chunk_texts
    )
    score = matched / len(expected_keywords)
    logger.debug(
        "retrieval_precision: %d/%d keywords found → %.3f",
        matched, len(expected_keywords), score,
    )
    return round(score, 4)


# ── Context Precision (RAGAS) ─────────────────────────────────────────────────

def context_precision(chunks: list[dict], expected_keywords: list[str]) -> float:
    """
    RAGAS-style context precision: of the chunks the retriever returned, what
    fraction are actually relevant (contain at least one expected keyword)?

    High precision = little noise in the retrieved context.
    Returns 0.0 if there's no ground truth or no chunks.
    """
    if not expected_keywords or not chunks:
        return 0.0
    kws      = [k.lower() for k in expected_keywords]
    relevant = sum(
        1 for c in chunks
        if any(kw in (c.get("parent_text") or c.get("text") or "").lower() for kw in kws)
    )
    score = relevant / len(chunks)
    logger.debug("context_precision: %d/%d chunks relevant → %.3f", relevant, len(chunks), score)
    return round(score, 4)


# ── Context Recall (RAGAS) ────────────────────────────────────────────────────

def context_recall(chunks: list[dict], expected_keywords: list[str]) -> float:
    """
    RAGAS-style context recall: of the ground-truth facts (expected_keywords),
    what fraction were actually covered by the retrieved context?

    High recall = the retriever didn't miss the facts needed to answer.
    (This is what the older retrieval_precision() body really measured — by the
    RAGAS definition keyword-coverage of the ground truth is recall, not precision.)
    """
    return retrieval_precision(chunks, expected_keywords)


# ── Faithfulness (LLM-as-judge) ───────────────────────────────────────────────

async def faithfulness_score(
    query: str,
    response: str,
    chunks: list[dict],
    provider=None,
) -> float:
    """
    LLM-as-judge: does the response stay faithful to the retrieved evidence?

    The LLM is given:
      - The retrieved context chunks (evidence)
      - The generated response
    It must identify any claims in the response that CANNOT be verified from
    the context. Returns a JSON with supported_claims, unsupported_claims, score.

    Returns 1.0 if the LLM call fails (conservative: don't penalize on error).
    Returns 1.0 if no chunks were provided (nothing to check against).
    """
    if not chunks or not response.strip():
        return 1.0

    # Format the evidence for the judge prompt
    evidence = "\n\n".join(
        f"[Chunk {i+1}]: {(c.get('parent_text') or c.get('text') or '')[:500]}"
        for i, c in enumerate(chunks[:6])   # cap at 6 chunks for token budget
    )

    try:
        from backend.core.config_loader import config

        prompt = config.get_prompt(
            "faithfulness_judge",
            query=query,
            response=response[:800],
            evidence=evidence,
        )
        system = config.get_prompt("system_prompt")

        if provider is None:
            from backend.providers.factory import LLMFactory
            provider = LLMFactory.get_provider()

        resp = await provider.generate_text(prompt, system, temperature=0.0, max_tokens=300)
        raw  = resp.text.strip()

        # Extract JSON from the response
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not match:
            logger.warning("faithfulness_score: no JSON in LLM output — returning 1.0")
            return 1.0

        data           = json.loads(match.group(0))
        supported      = int(data.get("supported_claims",   1))
        unsupported    = int(data.get("unsupported_claims", 0))
        total          = supported + unsupported
        score          = data.get("score")

        if score is not None:
            score = float(score)
        elif total > 0:
            score = supported / total
        else:
            score = 1.0

        logger.info(
            "faithfulness_score: supported=%d unsupported=%d → %.3f",
            supported, unsupported, score,
        )
        return round(float(score), 4)

    except Exception:
        logger.exception("faithfulness_score: LLM judge call failed — returning 1.0")
        return 1.0


# ── Answer Relevancy ──────────────────────────────────────────────────────────

def answer_relevancy(query: str, response: str) -> float:
    """
    Cosine similarity between the query embedding and the response embedding.

    High similarity (> 0.7) = the response is topically aligned with the question.
    Low similarity (< 0.4)  = the response drifted off-topic.

    Returns 0.0 on error or empty inputs.
    """
    if not query.strip() or not response.strip():
        return 0.0

    try:
        from backend.rag.retriever import _get_embed_model
        import numpy as np

        model       = _get_embed_model()
        embeddings  = model.encode([query, response[:500]], show_progress_bar=False)
        q_vec, r_vec = embeddings[0], embeddings[1]

        denom = float(np.linalg.norm(q_vec) * np.linalg.norm(r_vec))
        if denom == 0.0:
            return 0.0

        score = float(np.dot(q_vec, r_vec) / denom)
        logger.debug("answer_relevancy: cosine=%.3f for query='%s...'", score, query[:40])
        return round(max(0.0, score), 4)

    except Exception:
        logger.exception("answer_relevancy: embedding failed — returning 0.0")
        return 0.0


# ── Answer Correctness (RAGAS) ────────────────────────────────────────────────

def answer_correctness(response: str, expected_response_keywords: list[str]) -> float:
    """
    RAGAS-style answer correctness. We have no free-text gold answer, so the
    labelled expected_response_keywords act as the ground-truth facts:

      factual  = fraction of expected fact-keywords present in the response
      semantic = cosine(embed(response), embed(joined expected keywords))
      correctness = 0.5 * factual + 0.5 * semantic

    Combines exact fact coverage with semantic closeness so a correct answer
    phrased differently still scores. Returns 0.0 with no ground truth.
    """
    if not expected_response_keywords or not response.strip():
        return 0.0
    resp_lower = response.lower()
    matched    = sum(1 for kw in expected_response_keywords if kw.lower() in resp_lower)
    factual    = matched / len(expected_response_keywords)
    semantic   = answer_relevancy(" ".join(expected_response_keywords), response)
    score      = round(0.5 * factual + 0.5 * semantic, 4)
    logger.debug(
        "answer_correctness: factual=%.3f semantic=%.3f → %.3f", factual, semantic, score,
    )
    return score


# ── Intent Accuracy ───────────────────────────────────────────────────────────

def intent_accuracy(detected_intent: str, expected_intent: str) -> bool:
    """Return True if the graph routed to the correct agent for this query."""
    return detected_intent.strip().lower() == expected_intent.strip().lower()


# ── Persistence ───────────────────────────────────────────────────────────────

def save_eval_result(result: dict[str, Any]) -> None:
    """
    Append one evaluation result to data/eval_results.jsonl.

    Each line is a self-contained JSON object — no need to parse the whole file
    to add a new result. The admin page reads all lines and aggregates them.
    """
    try:
        _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _RESULTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")
    except Exception:
        logger.exception("save_eval_result: failed to write to %s", _RESULTS_PATH)


def load_eval_results() -> list[dict]:
    """
    Load all evaluation results from eval_results.jsonl.
    Returns [] if file doesn't exist or can't be read.
    """
    if not _RESULTS_PATH.exists():
        return []
    try:
        results = []
        with _RESULTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        return results
    except Exception:
        logger.exception("load_eval_results: failed to read %s", _RESULTS_PATH)
        return []


# ── Composite scorer ──────────────────────────────────────────────────────────

async def evaluate_query(
    eval_item: dict,
    retrieved_chunks: list[dict],
    response: str,
    detected_intent: str,
    provider=None,
) -> dict[str, Any]:
    """
    Run all three metrics for one eval_item and return a result dict.

    eval_item must have keys: id, query, expected_keywords,
    expected_response_keywords, intent, category, project.
    """
    query    = eval_item["query"]
    run_id   = str(uuid.uuid4())[:8]

    expected_kw     = eval_item.get("expected_keywords", [])
    expected_resp_kw = eval_item.get("expected_response_keywords", [])

    precision   = context_precision(retrieved_chunks, expected_kw)
    recall      = context_recall(retrieved_chunks, expected_kw)
    faithfulness = await faithfulness_score(query, response, retrieved_chunks, provider)
    relevancy   = answer_relevancy(query, response)
    correctness = answer_correctness(response, expected_resp_kw)
    intent_ok   = intent_accuracy(detected_intent, eval_item.get("intent", "cross_source"))

    # Composite — retrieval quality (precision+recall) and answer quality
    # (faithfulness, relevancy, correctness) each carry weight.
    composite = round(
        0.20 * precision + 0.20 * recall + 0.25 * faithfulness
        + 0.15 * relevancy + 0.20 * correctness,
        4,
    )

    result = {
        "run_id":          run_id,
        "eval_id":         eval_item["id"],
        "category":        eval_item.get("category", ""),
        "intent_expected": eval_item.get("intent", ""),
        "intent_detected": detected_intent,
        "intent_correct":  intent_ok,
        "query":           query,
        "response_snippet": response[:200],
        "chunks_retrieved": len(retrieved_chunks),
        "precision":       precision,
        "recall":          recall,
        "faithfulness":    faithfulness,
        "relevancy":       relevancy,
        "correctness":     correctness,
        "composite":       composite,
        "timestamp":       datetime.now().isoformat(),
        "project":         eval_item.get("project", ""),
    }

    logger.info(
        "evaluate_query: id=%s precision=%.2f recall=%.2f faithfulness=%.2f "
        "relevancy=%.2f correctness=%.2f composite=%.2f intent_ok=%s",
        eval_item["id"], precision, recall, faithfulness, relevancy, correctness, composite, intent_ok,
    )
    return result
