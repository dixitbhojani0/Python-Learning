"""
backend/agents/risk_agent.py

Sprint Risk Detection Agent.

Combines live Jira ticket data (MCP) with sprint docs (RAG) to compute
a structured risk score using chain-of-thought reasoning.

Why CoT + JSON output here (not prose)?
  Risk scores are numeric decisions — they need auditable reasoning.
  A plain prose answer ("looks risky") can't be acted on.
  A structured JSON with score + blockers + recommendation gives the
  manager something concrete: who to escalate to, what the number means.

Design: same dependency-injection pattern as CrossSourceAgent.
  Pass mock retriever + mock LLM in tests — no real models needed.
"""
import asyncio
import logging

try:
    from langsmith import traceable
except ImportError:
    def traceable(fn=None, **_kw):
        return fn if fn is not None else (lambda f: f)

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.core.prompt_safety import safety_guard
from backend.mcp_client.client import call_mcp_tool
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


# ── Context formatters ─────────────────────────────────────────────────────────

def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "No sprint documentation found."
    lines = []
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"[Source {i}: {chunk.source} ({chunk.doc_type})]")
        lines.append(content[:800])   # cap each chunk to stay within token budget
    return "\n\n".join(lines)


def _format_jira_context(sprint_board: dict, blocked_tickets: list[dict]) -> str:
    """
    Format sprint board stats + blocked ticket list for the CoT prompt.

    sprint_board  — dict returned by get_sprint_board() with summary stats
                    (total_tickets, done, completion_pct, days_remaining, etc.)
    blocked_tickets — list of ticket dicts returned by get_blocked_tickets()
                    (individual records with id, title, blockers, assignee)
    """
    if not sprint_board and not blocked_tickets:
        return "No Jira data available."

    lines = []

    if sprint_board:
        lines += [
            f"Sprint: {sprint_board.get('sprint', 'N/A')}",
            f"Goal: {sprint_board.get('goal', 'N/A')}",
            f"Total tickets: {sprint_board.get('total_tickets', 0)}",
            f"Done: {sprint_board.get('done', 0)}",
            f"Blocked: {sprint_board.get('blocked', 0)}",
            f"Completion: {sprint_board.get('completion_pct', 0)}%",
            f"Days remaining: {sprint_board.get('days_remaining', 'N/A')}",
        ]

    if blocked_tickets:
        lines.append("\nBlocked tickets — AUTHORITATIVE LIVE LIST (use ONLY these for the blockers field):")
        for t in blocked_tickets:
            raw_blockers  = "; ".join(t.get("blockers", [])) or "reason not specified"
            safe_title    = safety_guard.sanitize(t.get("title", ""))
            safe_assignee = safety_guard.sanitize(t.get("assignee", "unassigned"))
            safe_blockers = safety_guard.sanitize(raw_blockers)
            lines.append(
                f"  - [{t['id']}] {safe_title}"
                f" (assignee: {safe_assignee}, reason: {safe_blockers})"
            )
        lines.append(
            "Any ticket ID NOT in this list is NOT currently blocked — "
            "do NOT add it to the blockers field even if sprint docs mention it."
        )
    else:
        lines.append(
            "\nBlocked tickets: NONE — no tickets are currently blocked in Jira. "
            "Sprint documents may reference old resolved blockers; ignore them. "
            "The blockers field in the JSON must be an empty list []."
        )

    return "\n".join(lines)


def _format_pr_context(prs: list[dict]) -> str:
    """Format open PRs so the LLM can weigh stalled / failing-CI PRs as delivery risk."""
    if not prs:
        return "No open PRs."
    lines = []
    for pr in prs:
        reviewers = ", ".join(pr.get("reviewers", [])) or "NONE ASSIGNED"
        lines.append(
            f"- [{pr.get('id', '?')}] {safety_guard.sanitize(pr.get('title', ''))} "
            f"(CI: {pr.get('ci_status', 'unknown')}, reviewers: {reviewers})"
        )
    return "\n".join(lines)


# ── Deterministic risk calculator (used when LLM JSON parse fails) ────────────

def _compute_risk_from_jira(sprint_board: dict, blocked_tickets: list[dict]) -> dict:
    """
    Compute risk_data dict directly from Jira numbers — no LLM needed.

    Used as fallback when generate_structured returns parse_error=True so the
    user always gets a real score rather than "no data available" prose.
    Returns {} only when sprint_board has no ticket data at all.
    """
    if not sprint_board:
        return {}
    total   = sprint_board.get("total_tickets", 0)
    if not total:
        return {}
    done    = sprint_board.get("done", 0)
    blocked = sprint_board.get("blocked", len(blocked_tickets))
    pct     = sprint_board.get("completion_pct", round(done / total * 100))
    blocker_ratio = blocked / total
    score   = round(blocker_ratio * 50 + (1 - pct / 100) * 50)
    level   = "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW"
    blockers = [
        f"[{t.get('id', '?')}] {t.get('title', '')} (assignee: {t.get('assignee', 'unassigned')})"
        for t in blocked_tickets
    ]
    logger.info("RiskAgent: computed risk directly — score=%d level=%s", score, level)
    return {
        "risk_score":    score,
        "risk_level":    level,
        "completion_pct": pct,
        "blocked_count": blocked,
        "total_tickets": total,
        "days_remaining": sprint_board.get("days_remaining"),
        "blockers":      blockers,
        "pr_risks":      [],
        "recommendation": "",
    }


# ── Response formatter ─────────────────────────────────────────────────────────

def _format_risk_response(risk_data: dict) -> str:
    """
    Convert the parsed risk JSON into readable markdown for the chat UI.

    The persona layer will later rewrite this in role-appropriate language,
    so we produce a neutral, fact-first format here.
    """
    score       = risk_data.get("risk_score", "N/A")
    level       = risk_data.get("risk_level", "UNKNOWN")
    completion  = risk_data.get("completion_pct", "N/A")
    blocked     = risk_data.get("blocked_count", 0)
    total       = risk_data.get("total_tickets", 0)
    days        = risk_data.get("days_remaining")
    blockers    = risk_data.get("blockers", [])
    pr_risks    = risk_data.get("pr_risks", [])
    recommend   = risk_data.get("recommendation", "")

    level_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(level, "⚪")

    lines = [
        f"## Sprint Risk Assessment",
        f"",
        f"**Risk Score: {score}/100 — {level_emoji} {level}**",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Sprint Completion | {completion}% |",
        f"| Blocked Tickets | {blocked} of {total} |",
    ]

    if days is not None:
        lines.append(f"| Days Remaining | {days} |")

    if blockers:
        lines += ["", "**Active Blockers:**"]
        for b in blockers:
            if isinstance(b, dict):
                tid  = b.get("ticket_id") or b.get("id", "?")
                desc = b.get("description") or b.get("title", "")
                lines.append(f"- [{tid}] {desc}")
            else:
                lines.append(f"- {b}")

    if pr_risks:
        lines += ["", "**PRs adding delivery risk:**"]
        for p in pr_risks:
            if isinstance(p, dict):
                pid   = p.get("pr_id") or p.get("id", "?")
                desc  = p.get("description") or p.get("title", "")
                issue = p.get("issue", "")
                lines.append(f"- [{pid}] {desc}" + (f" — {issue}" if issue else ""))
            else:
                lines.append(f"- {p}")

    if recommend:
        lines += ["", f"**Recommended Action:** {recommend}"]

    return "\n".join(lines)


# ── Agent class ────────────────────────────────────────────────────────────────

class RiskAgent(BaseAgent):
    """
    Sprint risk detection agent.

    Flow:
      1. RAG — retrieve sprint docs (goals, velocity, story counts)
      2. MCP — fetch live Jira ticket board (current status, blockers)
      3. CoT prompt — LLM reasons step-by-step and outputs structured JSON
      4. Parse JSON → compute final risk assessment
      5. Return AgentPayload (no HITL — risk reports are read-only)
    """

    def __init__(self, retriever: HybridRetriever, llm, config_loader=None, mcp_registry=None):
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    async def _fetch_jira_data(self, project: str) -> tuple[dict, list[dict]]:
        """
        Fetch sprint board stats AND blocked ticket list in parallel.

        get_sprint_board() → dict with summary stats (total, done, completion_pct, days_remaining)
        get_blocked_tickets() → list of ticket dicts (id, title, blockers, assignee)

        Two separate calls because they return different shapes:
          - sprint board gives us the numbers for the risk formula
          - blocked tickets give us the specific items to list in the response

        Falls back to ({}, []) if MCP is unavailable or any call fails.
        """
        try:
            # Real MCP: the two tools the risk formula needs, fetched in parallel.
            results = await asyncio.gather(
                call_mcp_tool("jira_get_sprint_board", {"project": project}),
                call_mcp_tool("jira_get_blocked_tickets", {"project": project}),
                return_exceptions=True,
            )
            sprint_board = results[0] if isinstance(results[0], dict) else {}
            # call_mcp_tool may return a dict (single item) or list — normalize to list
            raw_blocked  = results[1]
            if isinstance(raw_blocked, list):
                blocked_tickets = raw_blocked
            elif isinstance(raw_blocked, dict) and raw_blocked.get("id"):
                blocked_tickets = [raw_blocked]
            else:
                blocked_tickets = []

            logger.info(
                "RiskAgent: sprint board=%s | blocked_tickets=%s",
                sprint_board, blocked_tickets,
            )
            return sprint_board, blocked_tickets
        except Exception:
            logger.exception("RiskAgent: Jira MCP fetch failed — risk score will use RAG only")
            return {}, []

    async def _fetch_open_prs(self) -> list[dict]:
        """
        Fetch open PRs from GitHub MCP so the risk reasoning can weigh stalled
        (no reviewer) or failing-CI PRs as additional delivery risk.

        Returns [] (non-fatal) if GitHub MCP is unavailable or the call fails —
        risk assessment still works on Jira + RAG alone.
        """
        try:
            prs = await call_mcp_tool("github_list_open_prs", {})
            if not isinstance(prs, list):
                prs = []
            logger.info("RiskAgent: %d open PRs fetched for risk weighting", len(prs))
            return prs[:8]   # cap to avoid prompt bloat
        except Exception:
            logger.exception("RiskAgent: GitHub PR fetch failed — continuing without PRs")
            return []

    @traceable(name="risk_agent", run_type="chain")
    async def run(self, state: SDLCState) -> AgentPayload:
        """
        Execute sprint risk assessment.

        Steps 1–5 as documented in class docstring.
        On any LLM/parse failure: returns a safe fallback response
        rather than crashing the graph.
        """
        query   = state["query"]
        project = state["project_id"]

        logger.info("RiskAgent.run: project='%s' query='%s...'", project, query[:60])

        # ── Step 1: RAG — sprint docs ─────────────────────────────────────────
        # "sprint risk" retrieves sprint planning docs, velocity history, sprint goals
        safe_query = safety_guard.sanitize(query)
        rag_query = f"sprint risk delivery blockers velocity {safe_query}"
        chunks, confidence = self.retriever.retrieve(rag_query, project)

        logger.info("RiskAgent: %d RAG chunks, top confidence=%.3f", len(chunks), confidence)

        # ── Step 2: MCP — live Jira data + open PRs ──────────────────────────
        sprint_board, blocked_tickets = await self._fetch_jira_data(project)
        open_prs = await self._fetch_open_prs()

        # ── Hallucination guard ──────────────────────────────────────────
        # Abort early if no sprint docs AND no Jira data — nothing to reason over.
        # RiskAgent can work with Jira alone (live data) — RAG is a bonus.
        low_conf_payload = self._low_confidence_guard(confidence, chunks, query)
        if low_conf_payload is not None and not sprint_board and not blocked_tickets:
            logger.warning("RiskAgent: no RAG context and no Jira data — returning not-found")
            return low_conf_payload

        # ── Step 3: Build CoT prompt ──────────────────────────────────────────
        system_prompt  = self.config.get_prompt("system_prompt")
        reasoning_tmpl = self.config.get_prompt(
            "risk_agent_reasoning",
            rag_context=_format_rag_context(chunks),
            jira_context=_format_jira_context(sprint_board, blocked_tickets),
            pr_context=_format_pr_context(open_prs),
        )

        # ── Step 4: Call LLM via generate_structured (provider handles JSON extraction) ──
        temperature = self.config.get_temperature("agent_reasoning")   # 0.1
        jira_ctx = _format_jira_context(sprint_board, blocked_tickets)
        logger.info("RiskAgent: reasoning_tmpl_len=%d jira_context=\n%s", len(reasoning_tmpl), jira_ctx)
        resp      = await self.llm.generate_structured(reasoning_tmpl, system_prompt, temperature, 2000)
        risk_data = resp.structured if not resp.parse_error else {}
        logger.info(
            "RiskAgent: is_empty=%s parse_error=%s structured=%s | raw_text=%.600s",
            resp.is_empty, resp.parse_error, resp.structured, resp.text,
        )

        # ── Step 5: Handle response ───────────────────────────────────────────
        if resp.is_empty:
            logger.warning("RiskAgent: LLM returned empty response — possible rate limit or quota exhausted")
            risk_data = _compute_risk_from_jira(sprint_board, blocked_tickets)
            final_response = (
                _format_risk_response(risk_data) if risk_data
                else "I'm temporarily unavailable — please try again in a moment.\n\nIf the issue persists, contact your system administrator."
            )
        elif resp.parse_error or not resp.structured:
            # LLM responded but not in JSON — compute deterministically from Jira data.
            logger.warning("RiskAgent: JSON parse failed — computing risk directly from Jira data")
            risk_data = _compute_risk_from_jira(sprint_board, blocked_tickets)
            final_response = (
                _format_risk_response(risk_data) if risk_data
                else "I could not compute a risk score — no sprint data is currently available."
            )
        else:
            risk_data      = resp.structured
            final_response = _format_risk_response(risk_data)


        # ── Collect sources ───────────────────────────────────────────────────
        all_sources = list({c.source for c in chunks})
        if sprint_board or blocked_tickets:
            all_sources.append("jira_live")

        return AgentPayload(
            agent_name="risk",
            confidence=confidence,
            summary=final_response[:200],
            structured={
                "final_response":   final_response,
                "skip_persona":     True,   # risk table already has manager-ready metrics; persona rewrites hallucinate sprint goals
                "risk_data":        risk_data,
                "rag_chunks":       [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in chunks
                ],
                "sprint_board":     sprint_board,
                "blocked_tickets":  blocked_tickets,
            },
            sources=all_sources,
        )
