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
        lines.append("\nBlocked tickets (verified live from Jira):")
        for t in blocked_tickets:
            raw_blockers  = "; ".join(t.get("blockers", [])) or "reason not specified"
            safe_title    = safety_guard.sanitize(t.get("title", ""))
            safe_assignee = safety_guard.sanitize(t.get("assignee", "unassigned"))
            safe_blockers = safety_guard.sanitize(raw_blockers)
            lines.append(
                f"  - [{t['id']}] {safe_title}"
                f" (assignee: {safe_assignee}, reason: {safe_blockers})"
            )
    else:
        lines.append(
            "\nBlocked tickets: NONE — no tickets are currently blocked in Jira. "
            "Sprint documents may reference old resolved blockers; ignore them."
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
            lines.append(f"- {b}")

    if pr_risks:
        lines += ["", "**PRs adding delivery risk:**"]
        for p in pr_risks:
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
            sprint_board    = results[0] if isinstance(results[0], dict) else {}
            blocked_tickets = results[1] if isinstance(results[1], list) else []

            logger.info(
                "RiskAgent: sprint board fetched, %d blocked tickets",
                len(blocked_tickets),
            )
            return sprint_board, blocked_tickets
        except Exception:
            logger.exception("RiskAgent: Jira MCP fetch failed — risk score will use RAG only")
            return {}, []

    async def _fetch_open_prs(self, project: str) -> list[dict]:
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
        open_prs = await self._fetch_open_prs(project)

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
        resp      = await self.llm.generate_structured(reasoning_tmpl, system_prompt, temperature, 1000)
        risk_data = resp.structured if not resp.parse_error else {}

        # ── Step 5: Handle response ───────────────────────────────────────────
        if resp.is_empty:
            logger.warning("RiskAgent: LLM returned empty response — possible rate limit or quota exhausted")
            final_response = (
                "I'm temporarily unavailable — please try again in a moment.\n\n"
                "If the issue persists, contact your system administrator."
            )
            risk_data = {}
        elif resp.parse_error or not resp.structured:
            # LLM responded but not in JSON format — use honest fallback
            logger.warning("RiskAgent: JSON parse failed — returning fallback response")
            final_response = (
                "I could not compute a precise risk score from the available data.\n\n"
                "Based on the sprint documentation, this sprint shows signs of delivery risk.\n"
                "Please check Jira directly for the current blocker status."
            )
            risk_data = {}
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
