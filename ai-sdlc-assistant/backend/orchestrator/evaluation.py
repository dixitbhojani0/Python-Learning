"""
backend/orchestrator/evaluation.py

Live response evaluation + self-critique reflection, extracted from nodes.py so
the node module stays thin graph wiring. Called once per answer at the end of
the adapt_persona node — this is the single place faithfulness is computed;
the chat route reads the scores off the returned graph state.
"""
import datetime
import logging
import uuid

from backend.core.config_loader import config
from backend.orchestrator.state import SDLCState
from backend.persona.adapter import PersonaAdapter

logger = logging.getLogger(__name__)

# Reflect only on genuinely low-faith answers. Read from llm.yaml > evaluation.
REFLECT_FAITH_THRESHOLD: float = float(
    config.get_llm_config().get("evaluation", {}).get("reflect_faithfulness_threshold", 0.45)
)


def _used_mcp(state: SDLCState) -> bool:
    """True if any agent payload sourced live MCP data (sources like 'mcp:<tool>').

    Reflection must skip these: MCP data isn't in rag_chunks, so the judge scores
    it low even when it's correct — regenerating from chunks alone would drop it.
    """
    for p in state.get("agent_payloads", []):
        if any(str(s).startswith("mcp:") for s in getattr(p, "sources", [])):
            return True
    return False


async def _reflect_and_revise(
    query: str, adapted: str, faith: float, rag_chunks: list, persona: str, provider,
) -> tuple[str, float]:
    """Self-critique retry for a low-faithfulness pure-RAG answer.

    Regenerate the answer grounded ONLY in the retrieved evidence, persona-adapt it,
    and re-score. Keep the revision ONLY if faithfulness actually improves — so this
    can never make the answer worse. One retry; degrades to the original on any error.
    """
    evidence = "\n\n".join(
        f"[Chunk {i+1}]: {(c.get('parent_text') or c.get('text') or '')[:500]}"
        for i, c in enumerate(rag_chunks[:6])   # mirror the judge's evidence window
    )
    prompt = config.get_prompt("reflection_retry", query=query, evidence=evidence, draft=adapted[:1200])
    if not prompt:
        return adapted, faith
    system = config.get_prompt("system_prompt")
    try:
        from backend.core.metrics import faithfulness_score

        resp = await provider.generate_text(prompt, system, temperature=0.0, max_tokens=600)
        revised_raw = resp.text.strip()
        if not revised_raw:
            return adapted, faith

        revised = await PersonaAdapter(llm=provider, config_loader=config).adapt(revised_raw, persona)
        new_faith = await faithfulness_score(query, revised, rag_chunks, provider)

        if new_faith > faith:
            logger.info("reflection: faithfulness %.2f → %.2f — using revised answer", faith, new_faith)
            return revised, new_faith
        logger.info("reflection: revised faith %.2f not better than %.2f — keeping original", new_faith, faith)
    except Exception:
        logger.exception("reflection: retry failed — keeping original answer")
    return adapted, faith


async def evaluate_response(
    state: SDLCState, adapted: str, persona: str, provider,
) -> tuple[str, float, float]:
    """Score the answer (faithfulness + relevancy), run one reflection retry when
    warranted, and persist the eval row. Returns (possibly revised answer, faith, relev).
    Fail-open: any evaluation error leaves the answer untouched with default scores.
    """
    query      = state.get("query", "")
    rag_chunks = state.get("rag_chunks", [])
    intent     = state.get("intent", "cross_source")
    project    = state.get("project_id", "")

    faith, relev = 1.0, 0.0   # fail-open defaults (match faithfulness_score's own behavior)
    try:
        from backend.core.metrics import answer_relevancy, faithfulness_score, save_eval_result

        faith = await faithfulness_score(query, adapted, rag_chunks, provider)
        relev = answer_relevancy(query, adapted)

        # Reflection loop: a pure-RAG answer that isn't grounded in its evidence
        # gets one self-critique retry. Skipped when MCP data was used (its facts
        # aren't in rag_chunks, so a low score there is a false positive).
        if faith < REFLECT_FAITH_THRESHOLD and rag_chunks and not _used_mcp(state):
            adapted, faith = await _reflect_and_revise(query, adapted, faith, rag_chunks, persona, provider)
            relev = answer_relevancy(query, adapted)

        if faith < REFLECT_FAITH_THRESHOLD:
            logger.warning(
                "eval: LOW FAITHFULNESS faith=%.2f relev=%.2f — response may be hallucinated. "
                "query='%s...' intent='%s'",
                faith, relev, query[:60], intent,
            )
        else:
            logger.info(
                "eval: faithfulness=%.2f relevancy=%.2f intent='%s' query='%s...'",
                faith, relev, intent, query[:50],
            )

        save_eval_result({
            "run_id":           str(uuid.uuid4())[:8],
            "eval_id":          "live",
            "category":         "live_request",
            "intent_expected":  intent,
            "intent_detected":  intent,
            "intent_correct":   True,
            "query":            query,
            "response_snippet": adapted[:200],
            "chunks_retrieved": len(rag_chunks),
            "precision":        0.0,
            "faithfulness":     faith,
            "relevancy":        relev,
            "composite":        round(0.35 * faith + 0.25 * relev, 4),
            "timestamp":        datetime.datetime.now().isoformat(),
            "project":          project,
            "flagged":          faith < REFLECT_FAITH_THRESHOLD,
        })
    except Exception:
        logger.exception("eval failed — ignoring")

    return adapted, faith, relev
