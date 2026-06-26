"""
backend/orchestrator/classifier.py

Intent classification for the LangGraph orchestrator.

Two classifiers — the first that succeeds wins:
  1. Fast path: ticket ID pattern (SDLC-5) → always cross_source, no LLM call.
  2. LLM supervisor: reads routing_descriptions from agents.yaml → picks agent.
  3. Keyword fallback: whole-word keyword matching → used when LLM fails for any reason.

The safe default intent for anything that doesn't match is "cross_source".
"""
import logging
import re

from backend.core.config_loader import config
from backend.providers.factory import LLMFactory

logger = logging.getLogger(__name__)


# ── Intent mapping ─────────────────────────────────────────────────────────────
# Maps agents.yaml agent keys → intent strings used throughout the graph.

AGENT_INTENT_MAP: dict[str, str] = {
    "cross_source_agent":      "cross_source",
    "risk_agent":              "risk",
    "ticket_agent":            "ticket",
    "pr_agent":                "pr_review",
    "release_readiness_agent": "release_readiness",
    "notify_agent":            "notify",
}

VALID_INTENTS: set[str] = set(AGENT_INTENT_MAP.values())


# ── Priority order for keyword fallback ───────────────────────────────────────
# More specific agents are checked first so a query like
# "are we ready to release the sprint?" hits release_readiness before risk.

_KEYWORD_PRIORITY_ORDER = [
    "notify_agent",           # explicit action verbs ("notify", "send message") win first
    "release_readiness_agent",
    "risk_agent",
    "pr_agent",               # PR/reviewer before ticket — "assign a reviewer" is a PR action,
    "ticket_agent",           # not a ticket assignment (ticket_agent owns the bare "assign" kw)
    "cross_source_agent",
]


def keyword_classify(query: str) -> str:
    """
    Keyword-based fallback classifier — used only when LLM supervisor fails.

    Matches whole-word patterns against trigger_keywords in agents.yaml.
    Defaults to 'cross_source' when nothing matches (safe fallback).
    """
    query_lower = query.lower()
    agents_cfg  = config.get_agents()

    for agent_key in _KEYWORD_PRIORITY_ORDER:
        agent_cfg = agents_cfg.get(agent_key, {})
        if not agent_cfg.get("enabled", False):
            continue
        for keyword in agent_cfg.get("trigger_keywords", []):
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            if re.search(pattern, query_lower):
                detected = AGENT_INTENT_MAP.get(agent_key, "cross_source")
                logger.debug(
                    "keyword_classify: matched '%s' → intent='%s'",
                    keyword, detected,
                )
                return detected

    logger.debug("keyword_classify: no match → defaulting to 'cross_source'")
    return "cross_source"


async def llm_classify(query: str) -> str:
    """
    LLM-based supervisor routing — the primary intent classifier.

    Reads routing_description from each agent in agents.yaml and asks the LLM
    to pick the right agent. Uses generate_structured() (JSON extraction, retry,
    fallback model) — no new provider methods needed.

    Falls back to keyword_classify() on any failure:
      - LLM rate limit or empty response
      - JSON parse error
      - Unknown agent value returned
      - Any unexpected exception

    Temperature 0.0 → deterministic. Max tokens 120 → only a short JSON object.
    """
    agents_cfg = config.get_agents()

    # Build agent description block for the prompt
    desc_lines: list[str] = []
    for agent_key, intent in AGENT_INTENT_MAP.items():
        agent_cfg = agents_cfg.get(agent_key, {})
        if not agent_cfg.get("enabled", False):
            continue
        desc = agent_cfg.get("routing_description", "").strip()
        if desc:
            desc_lines.append(f'- "{intent}": {desc}')

    if not desc_lines:
        logger.warning("llm_classify: no routing_description found in agents.yaml — keyword fallback")
        return keyword_classify(query)

    agent_descriptions = "\n".join(desc_lines)
    prompt = config.get_prompt(
        "supervisor_routing",
        agent_descriptions=agent_descriptions,
        query=query,
    )

    if not prompt:
        logger.warning("llm_classify: supervisor_routing prompt missing — keyword fallback")
        return keyword_classify(query)

    try:
        provider = LLMFactory.get_provider()
        resp = await provider.generate_structured(
            prompt=prompt,
            system="You are a routing agent. Output only valid JSON. No explanation.",
            temperature=0.0,
            max_tokens=120,
        )

        if resp.is_empty or resp.parse_error or not resp.structured:
            logger.warning(
                "llm_classify: LLM returned unusable response (empty=%s, parse_error=%s) — keyword fallback",
                resp.is_empty, resp.parse_error,
            )
            return keyword_classify(query)

        agent  = resp.structured.get("agent", "").strip()
        conf   = resp.structured.get("confidence", 0.0)
        reason = resp.structured.get("reason", "")

        if agent not in VALID_INTENTS:
            logger.warning(
                "llm_classify: LLM returned unknown agent '%s' — keyword fallback", agent,
            )
            return keyword_classify(query)

        logger.info(
            "llm_classify: '%s' → intent='%s' (confidence=%.2f) | %s",
            query[:60], agent, conf, reason,
        )
        return agent

    except Exception:
        logger.exception("llm_classify: unexpected error — keyword fallback")
        return keyword_classify(query)
