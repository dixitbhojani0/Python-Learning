"""
backend/agents/release_readiness_agent.py

Release Readiness Agent — go/no-go decision with HITL approval.

This agent ALWAYS sets hitl_required=True because releasing is a
high-stakes irreversible action. The HITL gate in the graph will present
the verdict to the user with Approve/Reject buttons before any action
is taken.

Data sources:
  - RAG: sprint docs + version_policies/ (compliance, semver rules)
  - MCP Jira: open P0/P1 critical tickets
  - MCP GitHub: open PRs, failing CI

CoT + Structured JSON output (same reasoning chain as RiskAgent, but
focused on a binary go/no-go decision rather than a numeric score).
"""
import asyncio
import json
import logging
import re

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


# ── JSON extraction (same helper pattern as RiskAgent) ────────────────────────

def _parse_json_block(text: str) -> dict:
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        json_str = match.group(0)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("ReleaseReadinessAgent: failed to parse JSON from LLM output")
        return {}


# ── Context formatters ─────────────────────────────────────────────────────────

def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "No sprint or policy documentation found."
    lines = []
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"[Source {i}: {chunk.source} ({chunk.doc_type})]")
        lines.append(content[:800])
    return "\n\n".join(lines)


def _format_jira_context(tickets: list[dict]) -> str:
    if not tickets:
        return "No Jira data available."
    lines = []
    for t in tickets:
        status   = t.get("status", "UNKNOWN")
        priority = t.get("priority", "MEDIUM")
        lines.append(
            f"- [{t['id']}] [P:{priority}] [{status}] {t['title']}"
            f" (assignee: {t.get('assignee', 'unassigned')})"
        )
    return "\n".join(lines)


def _format_github_context(prs: list[dict]) -> str:
    if not prs:
        return "No GitHub PR data available."
    lines = []
    for pr in prs:
        status = pr.get("status", "UNKNOWN")
        lines.append(
            f"- PR #{pr.get('id', '?')} [{status}] {pr.get('title', 'N/A')}"
            f" (author: {pr.get('author', 'unknown')})"
        )
    return "\n".join(lines)


# ── Response formatter ─────────────────────────────────────────────────────────

def _format_release_response(release_data: dict) -> str:
    """
    Format the parsed release readiness JSON into a HITL proposal card.

    This text will appear in the Chainlit UI alongside Approve/Reject buttons.
    The tone must be clear and precise — this is a release decision, not a chat.
    """
    verdict   = release_data.get("verdict", "NO_GO")
    summary   = release_data.get("summary", "")
    blockers  = release_data.get("blockers", [])
    warnings  = release_data.get("warnings", [])
    open_crit = release_data.get("open_critical_tickets", 0)
    open_prs  = release_data.get("open_prs", 0)
    complete  = release_data.get("sprint_complete", False)
    confidence = release_data.get("confidence", 0.0)

    verdict_emoji = "✅" if verdict == "GO" else "❌"

    lines = [
        f"## Release Readiness Assessment",
        f"",
        f"**Verdict: {verdict_emoji} {verdict}** (confidence: {confidence:.0%})",
        f"",
        f"| Check | Status |",
        f"|-------|--------|",
        f"| Sprint Complete | {'✅ Yes' if complete else '❌ No'} |",
        f"| Open Critical Tickets | {open_crit} |",
        f"| Open PRs | {open_prs} |",
    ]

    if summary:
        lines += ["", summary]

    if blockers:
        lines += ["", "**Blockers (must resolve before release):**"]
        for b in blockers:
            lines.append(f"- ❌ {b}")

    if warnings:
        lines += ["", "**Warnings (non-blocking):**"]
        for w in warnings:
            lines.append(f"- ⚠️ {w}")

    lines += [
        "",
        "---",
        "_This is a release proposal. Click **Approve** to confirm release readiness "
        "or **Reject** to cancel._",
    ]

    return "\n".join(lines)


# ── Agent class ────────────────────────────────────────────────────────────────

class ReleaseReadinessAgent(BaseAgent):
    """
    Release readiness assessment — always produces a HITL proposal.

    Flow:
      1. RAG — sprint docs + version_policies/ (semver and API policy)
      2. MCP Jira — all tickets (check for open P0/P1)
      3. MCP GitHub — all open PRs (check for unmerged critical PRs)
      4. CoT prompt — LLM assesses all checks and outputs JSON verdict
      5. Parse JSON → format HITL proposal card
      6. Return AgentPayload(hitl_required=True)
    """

    def __init__(self, retriever: HybridRetriever, llm, config_loader=None, mcp_registry=None):
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    async def _fetch_mcp_data(self, project: str) -> tuple[dict, list[dict], list[dict]]:
        """
        Fetch sprint board stats, ticket list, and GitHub PRs in parallel.

        Returns (sprint_board, jira_tickets, github_prs).
          sprint_board  — dict with completion stats (from get_sprint_board)
          jira_tickets  — list of ticket dicts for P0/P1 check (from search_tickets)
          github_prs    — list of open PR dicts (from list_open_prs)

        asyncio.gather with return_exceptions=True per resilience_standards.md.
        """
        if self.mcp is None:
            return {}, [], []

        try:
            results = await asyncio.gather(
                self.mcp.get("jira").get_sprint_board(project),
                self.mcp.get("jira").search_tickets("high blocked open", project),
                self.mcp.get("github").list_open_prs(),
                return_exceptions=True,
            )
            sprint_board = results[0] if not isinstance(results[0], Exception) else {}
            jira_tickets = results[1] if not isinstance(results[1], Exception) else []
            github_prs   = results[2] if not isinstance(results[2], Exception) else []

            logger.info(
                "ReleaseReadinessAgent: sprint board fetched, %d Jira tickets, %d GitHub PRs",
                len(jira_tickets), len(github_prs),
            )
            return sprint_board, jira_tickets, github_prs

        except Exception:
            logger.exception("ReleaseReadinessAgent: MCP fetch failed — using RAG only")
            return {}, [], []

    async def run(self, state: SDLCState) -> AgentPayload:
        """Execute release readiness assessment and return HITL proposal."""
        query   = state["query"]
        project = state["project_id"]

        logger.info("ReleaseReadinessAgent.run: project='%s'", project)

        # ── Step 1: RAG — sprint docs + version policy ────────────────────────
        rag_query = f"release readiness sprint completion version policy {query}"
        chunks, confidence = self.retriever.retrieve(rag_query, project)

        logger.info(
            "ReleaseReadinessAgent: %d RAG chunks, top confidence=%.3f",
            len(chunks), confidence,
        )

        # ── Step 2: MCP — Jira + GitHub ──────────────────────────────────────
        sprint_board, jira_tickets, github_prs = await self._fetch_mcp_data(project)

        # ── Step 3: Build CoT prompt ──────────────────────────────────────────
        # Combine sprint board stats with individual ticket list for the jira_context slot
        sprint_summary = (
            f"Sprint completion: {sprint_board.get('completion_pct', 'N/A')}% "
            f"({sprint_board.get('done', '?')}/{sprint_board.get('total_tickets', '?')} done, "
            f"{sprint_board.get('days_remaining', '?')} days remaining)\n"
        ) if sprint_board else ""

        system_prompt  = self.config.get_prompt("system_prompt")
        reasoning_tmpl = self.config.get_prompt(
            "release_readiness_reasoning",
            rag_context=_format_rag_context(chunks),
            jira_context=sprint_summary + _format_jira_context(jira_tickets),
            github_context=_format_github_context(github_prs),
        )

        # ── Step 4: Call LLM ─────────────────────────────────────────────────
        temperature = self.config.get_temperature("agent_reasoning")  # 0.1
        max_tokens  = 600

        tokens: list[str] = []
        async for token in self.llm.generate(reasoning_tmpl, system_prompt, temperature, max_tokens):
            tokens.append(token)

        llm_output = "".join(tokens)

        # ── Step 5: Parse JSON → HITL proposal ───────────────────────────────
        release_data = _parse_json_block(llm_output)

        if not release_data:
            logger.warning("ReleaseReadinessAgent: JSON parse failed — NO_GO fallback")
            release_data = {
                "verdict": "NO_GO",
                "confidence": 0.0,
                "blockers": ["Could not complete automated assessment — review manually."],
                "warnings": [],
                "open_critical_tickets": 0,
                "open_prs": len(github_prs),
                "sprint_complete": False,
                "summary": "Automated release readiness check could not be completed.",
            }

        final_response = _format_release_response(release_data)

        # ── Build HITL proposal ───────────────────────────────────────────────
        proposal = {
            "action":        "release_approval",
            "verdict":       release_data.get("verdict", "NO_GO"),
            "project":       project,
            "release_data":  release_data,
        }

        # ── Collect sources ───────────────────────────────────────────────────
        all_sources = list({c.source for c in chunks})
        if jira_tickets:
            all_sources.append("jira_live")
        if github_prs:
            all_sources.append("github_live")

        return AgentPayload(
            agent_name="release_readiness",
            confidence=confidence,
            summary=f"Release verdict: {release_data.get('verdict', 'NO_GO')}",
            structured={
                "final_response": final_response,
                "release_data":   release_data,
                "rag_chunks":     [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in chunks
                ],
                "jira_tickets":   jira_tickets,
                "github_prs":     github_prs,
            },
            sources=all_sources,
            hitl_required=True,
            hitl_proposal=proposal,
        )
