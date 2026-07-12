"""
backend/orchestrator/classifier.py

Intent classification for the LangGraph orchestrator.

Two classifiers — the first that succeeds wins:
  1. Fast path: ticket ID pattern (SDLC-5) → always cross_source, no LLM call.
  2. LLM supervisor: reads routing_descriptions from agents.yaml → picks agent.
  3. Keyword fallback: whole-word keyword matching → used when LLM fails for any reason.

The safe default intent for anything that doesn't match is "cross_source".

AGENT_INTENT_MAP and VALID_INTENTS are now derived from agents.yaml at runtime
(via get_agent_intent_map()). The convention is:
  - Each agent block may have an `intent` field.  If absent, the intent is
    derived by stripping a trailing `_agent` suffix from the agent key.
    e.g. risk_agent → "risk",  cross_source_agent → "cross_source".
  - Adding a new agent = add it to agents.yaml only. Zero changes here.
"""
import logging
import re

from backend.core.config_loader import config
from backend.providers.factory import LLMFactory

logger = logging.getLogger(__name__)


# ── Intent mapping (derived from agents.yaml) ───────────────────────────────────────
# Convention: intent = agent_cfg.get("intent") OR agent_key with "_agent" suffix stripped.
# e.g.  cross_source_agent → "cross_source",  risk_agent → "risk".
# This means adding a new agent block to agents.yaml is the ONLY required change.

_cached_intent_map: dict[str, str] | None = None


def get_agent_intent_map() -> dict[str, str]:
    """
    Build {agent_key: intent_string} from agents.yaml at first call, then cache.

    Convention (no explicit `intent` field needed in YAML):
        agent key          intent derived
        ─────────────────  ─────────────────────
        cross_source_agent cross_source
        risk_agent         risk
        ticket_agent       ticket
        pr_agent           pr_review  ← explicit `intent` field overrides convention
        release_readiness_agent release_readiness
        notify_agent       notify
    """
    global _cached_intent_map
    if _cached_intent_map is not None:
        return _cached_intent_map

    agents_cfg = config.get_agents()
    mapping: dict[str, str] = {}
    for agent_key, agent_cfg in agents_cfg.items():
        if not isinstance(agent_cfg, dict):
            continue
        if agent_cfg.get("intent"):          # explicit override wins
            intent = str(agent_cfg["intent"]).strip()
        else:                                # convention: strip _agent suffix
            intent = agent_key.removesuffix("_agent")
        mapping[agent_key] = intent

    _cached_intent_map = mapping
    logger.debug("classifier: built AGENT_INTENT_MAP from agents.yaml: %s", mapping)
    return mapping


# Module-level aliases (backward-compatible names used throughout the codebase)
AGENT_INTENT_MAP: dict[str, str] = {}      # populated lazily on first use
VALID_INTENTS:    set[str]       = set()   # populated lazily on first use


def _ensure_maps() -> None:
    """Populate module-level aliases if not yet built."""
    global AGENT_INTENT_MAP, VALID_INTENTS
    if not AGENT_INTENT_MAP:
        AGENT_INTENT_MAP.update(get_agent_intent_map())
        VALID_INTENTS.update(AGENT_INTENT_MAP.values())


# Trigger population at import time (safe — config is ready before any module is imported)
_ensure_maps()


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


def keyword_classify(query: str) -> list[str]:
    """
    Keyword-based fallback classifier — used only when LLM supervisor fails.

    Matches whole-word patterns against trigger_keywords in agents.yaml.
    Returns a list with one intent. Defaults to ['cross_source'] when nothing
    matches (safe fallback). Always returns a list for consistency with llm_classify.
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
                return [detected]

    logger.debug("keyword_classify: no match → defaulting to 'cross_source'")
    return ["cross_source"]


async def llm_classify(query: str) -> list[str]:
    """
    LLM-based supervisor routing — the primary intent classifier.

    Reads routing_description from each agent in agents.yaml and asks the LLM
    to pick the right agent(s). Now returns a list[str] to support multi-agent
    fan-out (e.g. "create a ticket AND notify the team" → ["ticket", "notify"]).

    Falls back to keyword_classify() on any failure:
      - LLM rate limit or empty response
      - JSON parse error
      - Unknown agent value returned
      - Any unexpected exception

    Temperature 0.0 → deterministic. Max tokens 150.
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
            max_tokens=150,
        )

        if resp.is_empty or resp.parse_error or not resp.structured:
            logger.warning(
                "llm_classify: LLM returned unusable response (empty=%s, parse_error=%s) — keyword fallback",
                resp.is_empty, resp.parse_error,
            )
            return keyword_classify(query)

        # Support both new list format {"agents": [...]} and legacy {"agent": "..."}.
        raw_agents = resp.structured.get("agents") or resp.structured.get("agent")
        conf       = resp.structured.get("confidence", 0.0)
        reason     = resp.structured.get("reason", "")

        # Normalise to list — LLM may return a string or a list
        if isinstance(raw_agents, str):
            raw_agents = [raw_agents]
        if not isinstance(raw_agents, list):
            logger.warning("llm_classify: unexpected 'agents' type %s — keyword fallback", type(raw_agents))
            return keyword_classify(query)

        # Validate every intent in the list; drop unknowns
        valid = [a.strip() for a in raw_agents if a.strip() in VALID_INTENTS]
        if not valid:
            logger.warning(
                "llm_classify: LLM returned no valid agents %s — keyword fallback", raw_agents,
            )
            return keyword_classify(query)

        logger.info(
            "llm_classify: '%s' → agents=%s (confidence=%.2f) | %s",
            query[:60], valid, conf, reason,
        )
        return valid

    except Exception:
        logger.exception("llm_classify: unexpected error — keyword fallback")
        return keyword_classify(query)
