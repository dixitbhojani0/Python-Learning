"""
scripts/evaluate.py

Automated evaluation runner for the AI SDLC Assistant.

Runs each query in data/eval_set.json through the full pipeline and
scores three dimensions per query:
  - Retrieval Precision: are the right chunks being retrieved?
  - Faithfulness:        does the response stay grounded in the evidence?
  - Answer Relevancy:    is the response on-topic for the question?

Usage:
  python scripts/evaluate.py                    # full pipeline (RAG + LLM)
  python scripts/evaluate.py --retrieval-only   # skip LLM calls (fast, checks RAG only)
  python scripts/evaluate.py --query eval_001   # run a single query by ID

Output:
  - Prints a results table to stdout
  - Appends each result to data/eval_results.jsonl (used by admin page)
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

# Resolve project root so imports work from any working directory
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))


def _load_eval_set(query_id: str | None = None) -> list[dict]:
    path = _root / "data" / "eval_set.json"
    if not path.exists():
        print(f"ERROR: eval_set.json not found at {path}")
        sys.exit(1)
    items = json.loads(path.read_text(encoding="utf-8"))
    if query_id:
        items = [i for i in items if i["id"] == query_id]
        if not items:
            print(f"ERROR: eval_id '{query_id}' not found in eval_set.json")
            sys.exit(1)
    return items


def _retrieve(query: str, project: str) -> tuple[list[dict], float, str]:
    """Run RAG retrieval and intent classification. Returns (chunks, confidence, intent)."""
    from backend.rag.retriever import HybridRetriever
    from backend.orchestrator.graph import _keyword_classify

    retriever = HybridRetriever()
    chunks, confidence = retriever.retrieve(query, project)

    # Serialize RetrievedChunk objects to plain dicts
    chunk_dicts = [
        {
            "text":        c.text,
            "parent_text": c.parent_text,
            "source":      c.source,
            "doc_type":    c.doc_type,
            "score":       c.score,
        }
        for c in chunks
    ]

    detected_intent = _keyword_classify(query)
    return chunk_dicts, confidence, detected_intent


async def _generate_response(query: str, chunks: list[dict]) -> str:
    """Call the LLM with retrieved chunks to produce a response."""
    from backend.providers.groq_provider import GroqProvider
    from backend.core.config_loader import config

    system   = config.get_prompt("system_prompt")
    persona  = config.get_prompt("persona_developer")
    rag_text = "\n\n".join(
        f"[Source {i+1}: {c['source']}]\n{c.get('parent_text') or c.get('text', '')[:600]}"
        for i, c in enumerate(chunks[:5])
    )

    prompt = f"{persona}\n\n## Retrieved Context\n{rag_text}\n\n## Question\n{query}"

    provider = GroqProvider()
    tokens: list[str] = []
    async for token in provider.generate(prompt, system, temperature=0.1, max_tokens=512):
        tokens.append(token)
    return "".join(tokens).strip()


async def _run_evaluation(
    items: list[dict],
    retrieval_only: bool,
) -> list[dict]:
    """Run the full evaluation loop over all items."""
    from backend.core.metrics import evaluate_query, save_eval_result

    results = []

    for item in items:
        print(f"\n  Running {item['id']}  [{item['category']}]  \"{item['query'][:60]}...\"")

        # ── Step 1: RAG retrieval + intent classification
        chunks, confidence, intent = _retrieve(item["query"], item["project"])
        print(f"    Retrieved {len(chunks)} chunks | rag_confidence={confidence:.3f} | intent={intent}")

        # ── Step 2: Generate response (unless retrieval-only mode)
        if retrieval_only:
            response = "[skipped — retrieval-only mode]"
        else:
            try:
                response = await _generate_response(item["query"], chunks)
                print(f"    Response: {response[:80]}...")
            except Exception as e:
                response = f"[LLM error: {e}]"
                print(f"    LLM error: {e}")

        # ── Step 3: Score all three dimensions
        provider = None if retrieval_only else None   # evaluate_query creates one internally
        result = await evaluate_query(
            eval_item=item,
            retrieved_chunks=chunks,
            response=response,
            detected_intent=intent,
            provider=provider,
        )
        result["rag_confidence"] = confidence

        # Print per-query scores
        print(
            f"    Precision={result['precision']:.2f}  "
            f"Faithfulness={result['faithfulness']:.2f}  "
            f"Relevancy={result['relevancy']:.2f}  "
            f"Composite={result['composite']:.2f}  "
            f"Intent={'OK' if result['intent_correct'] else 'WRONG'}"
        )

        save_eval_result(result)
        results.append(result)

    return results


def _print_summary(results: list[dict]) -> None:
    """Print an aggregated summary table to stdout."""
    if not results:
        return

    avg_p   = sum(r["precision"]    for r in results) / len(results)
    avg_f   = sum(r["faithfulness"] for r in results) / len(results)
    avg_r   = sum(r["relevancy"]    for r in results) / len(results)
    avg_c   = sum(r["composite"]    for r in results) / len(results)
    intent_ok = sum(1 for r in results if r["intent_correct"]) / len(results)

    print("\n" + "=" * 70)
    print("EVALUATION SUMMARY")
    print("=" * 70)
    print(f"  Queries evaluated : {len(results)}")
    print(f"  Retrieval Precision : {avg_p:.3f}  (target > 0.70)")
    print(f"  Faithfulness        : {avg_f:.3f}  (target > 0.80)")
    print(f"  Answer Relevancy    : {avg_r:.3f}  (target > 0.60)")
    print(f"  Composite Score     : {avg_c:.3f}  (target > 0.70)")
    print(f"  Intent Accuracy     : {intent_ok:.1%}")
    print("=" * 70)

    # Highlight worst-performing queries
    worst = sorted(results, key=lambda x: x["composite"])[:3]
    print("\nLowest composite scores (investigate these):")
    for r in worst:
        print(
            f"  {r['eval_id']}  composite={r['composite']:.2f}  "
            f"precision={r['precision']:.2f}  faithfulness={r['faithfulness']:.2f}  "
            f"relevancy={r['relevancy']:.2f}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(description="AI SDLC Assistant — Evaluation Runner")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Skip LLM response generation and faithfulness scoring (fast RAG-only check)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        metavar="EVAL_ID",
        help="Run only the specified eval item by ID (e.g. eval_001)",
    )
    args = parser.parse_args()

    items = _load_eval_set(args.query)

    mode = "retrieval-only" if args.retrieval_only else "full pipeline"
    print(f"\nAI SDLC Assistant — Evaluation ({mode})")
    print(f"Loaded {len(items)} query/queries from eval_set.json")
    print("-" * 70)

    results = asyncio.run(_run_evaluation(items, args.retrieval_only))

    _print_summary(results)
    print(f"Results appended to data/eval_results.jsonl\n")


if __name__ == "__main__":
    main()
