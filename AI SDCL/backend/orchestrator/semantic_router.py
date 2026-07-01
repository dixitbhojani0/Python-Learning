"""
backend/orchestrator/semantic_router.py

Semantic (embedding-based) intent router — the standard production pattern for
agent routing (see Aurelio semantic-router, Arize/Patronus agent-router guides).
Instead of an LLM call or static keyword matching, we pre-encode a few example
utterances per intent and route a query by nearest-neighbour cosine similarity.

Why this, not the alternatives:
  - vs LLM classifier: ~50-65x cheaper, ~50x faster, and DETERMINISTIC — and it
    spends zero LLM tokens (so it can't be knocked out by a provider rate limit).
  - vs static keywords: not exact-match. "spin up a ticket for the 500 error"
    embeds close to "create a ticket" even though it shares no keyword.

It is deliberately a HYBRID: the router answers only when it is confident
(score >= threshold AND a clear margin over the runner-up). Ambiguous /
out-of-distribution / compositional queries return None, and the caller falls
back to the LLM supervisor — which is what embedding routers are weak at.

Anchors come straight from agents.yaml `trigger_keywords` (the phrase ones) +
each agent's `routing_description`. No new config: the same phrases that were
static keywords become semantic anchors, used the right way.

ponytail: the two thresholds below ARE the calibration knob. Embedding scores are
empirical (encoder-, phrasing-dependent) — re-tune _MIN_SCORE/_MARGIN against a
validation set if routing drifts; don't expect a single magic constant.
"""
import logging

import numpy as np

from backend.core.config_loader import config
from backend.orchestrator.classifier import AGENT_INTENT_MAP, VALID_INTENTS
from backend.rag.retriever import embed_text, embed_texts_batch

logger = logging.getLogger(__name__)

# Route only when the best intent clears this cosine score AND beats the
# runner-up by this margin. Below either bar → return None → LLM fallback.
# Values are loaded from llm.yaml > routing.semantic_router at first use;
# the constants below are the hardcoded fallbacks for safety.
# Calibrated on real all-MiniLM scores: clean single-intent queries win by
# margin >= 0.25, while compositional "action + topic" queries land ~0.05 apart.
_SR_DEFAULTS = {"min_score": 0.42, "margin": 0.12}


def _sr_cfg() -> dict:
    """Read semantic router thresholds from llm.yaml (cached via config singleton)."""
    return config.get_llm_config().get("routing", {}).get("semantic_router", _SR_DEFAULTS)


def _anchor_phrases(agent_cfg: dict) -> list[str]:
    """Example utterances for one agent: multi-word trigger phrases + the routing
    description. Bare single-word keywords (jira/github/slack…) are dropped — they
    appear under several agents and make weak, ambiguous anchors."""
    phrases = [kw for kw in agent_cfg.get("trigger_keywords", []) if isinstance(kw, str) and " " in kw]
    desc = (agent_cfg.get("routing_description") or "").strip()
    if desc:
        phrases.append(desc)
    return phrases


class SemanticRouter:
    """Embedding nearest-neighbour intent router built from agents.yaml anchors."""

    def __init__(self) -> None:
        self._intents: list[str] = []          # parallel to self._matrix rows
        self._matrix: np.ndarray | None = None  # (n_anchors, dim), L2-normalized
        self._built = False

    def _build(self) -> None:
        """Encode every anchor once (lazy — first route() call). Cached for process life."""
        agents_cfg = config.get_agents()
        anchors: list[str] = []
        intents: list[str] = []
        for agent_key, intent in AGENT_INTENT_MAP.items():
            agent_cfg = agents_cfg.get(agent_key, {})
            if not agent_cfg.get("enabled", False):
                continue
            for phrase in _anchor_phrases(agent_cfg):
                anchors.append(phrase)
                intents.append(intent)

        if not anchors:
            logger.warning("SemanticRouter: no anchors found in agents.yaml — router disabled")
            self._built = True
            return

        self._matrix = np.asarray(embed_texts_batch(anchors), dtype=np.float32)
        self._intents = intents
        self._built = True
        logger.info("SemanticRouter: encoded %d anchors across %d intents",
                    len(anchors), len(set(intents)))

    def route(self, query: str) -> tuple[str | None, float]:
        """
        Return (intent, score) when confident, else (None, best_score).

        intent is the agent whose best-matching anchor is most similar to the
        query, provided it clears _MIN_SCORE and beats the next-best intent by
        _MARGIN. Otherwise None → caller should fall back to the LLM supervisor.
        """
        if not self._built:
            self._build()
        if self._matrix is None or not query.strip():
            return None, 0.0

        q = np.asarray(embed_text(query), dtype=np.float32)
        # Embeddings are L2-normalized (config embeddings.normalize=True), so the
        # dot product IS cosine similarity.
        sims = self._matrix @ q

        # Best score per intent (max over that intent's anchors).
        best_per_intent: dict[str, float] = {}
        for intent, score in zip(self._intents, sims):
            s = float(score)
            if s > best_per_intent.get(intent, -1.0):
                best_per_intent[intent] = s

        ranked = sorted(best_per_intent.items(), key=lambda kv: kv[1], reverse=True)
        top_intent, top_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

        sr = _sr_cfg()
        min_score = float(sr.get("min_score", _SR_DEFAULTS["min_score"]))
        margin    = float(sr.get("margin",    _SR_DEFAULTS["margin"]))

        if top_score >= min_score and (top_score - runner_up) >= margin and top_intent in VALID_INTENTS:
            logger.info("SemanticRouter: '%s' → %s (score=%.3f, margin=%.3f)",
                        query[:60], top_intent, top_score, top_score - runner_up)
            return top_intent, top_score

        logger.info("SemanticRouter: '%s' uncertain (top=%s %.3f, runner-up=%.3f) — LLM fallback",
                    query[:60], top_intent, top_score, runner_up)
        return None, top_score


# Process singleton — anchors encoded once on first use.
semantic_router = SemanticRouter()


def demo() -> None:
    """Self-check. Two contracts:
      - high-signal queries MUST route to the right intent (no LLM needed);
      - paraphrase/ambiguous queries must NEVER misroute — None (defer to LLM) is OK.
    A wrong non-None intent is the only real failure."""
    must_route = [
        ("Create a ticket: login page throws a 500 error on submit", "ticket"),
        ("What is the sprint risk right now?", "risk"),
        ("Are we ready to release? give me a go/no-go", "release_readiness"),
        ("Show me the open PRs that need code review", "pr_review"),
        ("Send a slack message to the team", "notify"),   # clean action, no topic collision
    ]
    defer_ok = [  # low signal / compositional — correct intent OR None (LLM), never wrong
        ("spin up a bug for the broken checkout flow", "ticket"),
        ("What caused the CORS error last week?", "cross_source"),
        # action verb + topic that collides with another intent's anchor: the
        # router must NOT confidently pick 'risk' here — defer to the LLM.
        ("Send a slack message to the team about the sprint status", "notify"),
    ]
    failures = 0
    for query, expected in must_route:
        intent, score = semantic_router.route(query)
        ok = intent == expected
        failures += not ok
        print(f"  [{'OK  ' if ok else 'FAIL'}] must-route {expected:<17} got={intent!s:<14} {score:.3f} | {query}")
    for query, expected in defer_ok:
        intent, score = semantic_router.route(query)
        ok = intent in (expected, None)   # deferral is acceptable; misroute is not
        failures += not ok
        tag = "OK  " if ok else "FAIL"
        note = "(deferred to LLM)" if intent is None else ""
        print(f"  [{tag}] defer-ok   {expected:<17} got={intent!s:<14} {score:.3f} {note} | {query}")
    assert failures == 0, f"{failures} routing failure(s)"
    print(f"\nPASS — {len(must_route)} routed deterministically, {len(defer_ok)} safely deferred, 0 misroutes")


if __name__ == "__main__":
    demo()
