"""
backend/agents/cross_source_agent.py

Cross-Source Correlation Agent — the primary agent for most queries.

Correlates evidence across all ingested sources (sprint docs, ADRs, Jira history,
Slack messages) using hybrid RAG retrieval + live MCP tool data.

Phase 5b: RAG + LLM only (mcp_registry=None).
Phase 6:  MCP connectors added — Jira + Slack called in parallel before LLM.

Design: dependency injection via __init__ keeps this class testable.
Pass a mock retriever + mock LLM in unit tests — no real models needed.
"""
import asyncio
import logging

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


# ── Prompt formatting helpers ─────────────────────────────────────────────────

def _format_conversation_history(messages: list[dict]) -> str:
    """
    Format prior conversation turns into a context block for the LLM.

    Appears BEFORE the RAG context so the LLM knows what was already discussed
    before reasoning over new evidence. Responses are pre-truncated to 300 chars
    by SessionStore so this section never dominates the token budget.
    """
    if not messages:
        return ""
    lines = ["## Conversation History (prior turns this session)"]
    for m in messages:
        lines.append(f"\nUser ({m.get('role', 'unknown')}): {m.get('query', '')}")
        lines.append(f"Assistant: {m.get('response', '')}")
    return "\n".join(lines)


def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a labelled context block for the LLM prompt."""
    if not chunks:
        return ""
    lines = ["## Retrieved Context (Sprint Docs, ADRs, Jira History, Slack)"]
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"\n### Source {i}: {chunk.source} ({chunk.doc_type})")
        lines.append(content)
    return "\n".join(lines)


def _format_mcp_context(jira_tickets: list[dict], slack_messages: list[dict]) -> str:
    """
    Format live MCP tool data into a labelled section for the LLM prompt.

    This section appears AFTER the RAG context — the LLM sees historical docs first,
    then current live state. This order helps it reason "here's what was planned,
    here's what's actually happening now."
    """
    if not jira_tickets and not slack_messages:
        return ""

    lines = ["## Live Tool Data (Current State)"]

    if jira_tickets:
        lines.append("\n### Jira: Current Tickets")
        for t in jira_tickets:
            status    = t.get("status", "UNKNOWN")
            blockers  = "; ".join(t.get("blockers", [])) or "none"
            lines.append(
                f"- [{t['id']}] [{status}] {t['title']}"
                f" (assignee: {t.get('assignee', 'unassigned')}, blockers: {blockers})"
            )

    if slack_messages:
        lines.append("\n### Slack: Recent #backend Messages")
        for m in slack_messages:
            lines.append(f"- {m.get('user', '?')} ({m.get('timestamp', '')[:10]}): {m['message']}")

    return "\n".join(lines)


# ── Agent class ───────────────────────────────────────────────────────────────

class CrossSourceAgent(BaseAgent):
    """
    Correlates evidence across all ingested data sources.

    This is the default agent — it handles all queries that don't match a
    more specific intent (ticket creation, risk scoring, PR review).
    """

    def __init__(self, retriever: HybridRetriever, llm, config_loader=None, mcp_registry=None):
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    async def _fetch_mcp_data(self, query: str, project: str) -> tuple[list[dict], list[dict]]:
        """
        Fetch live Jira tickets + Slack messages in parallel.

        Returns (jira_tickets, slack_messages).
        If MCP registry is not wired (Phase 5b) or a connector fails, returns empty lists.
        The agent degrades gracefully — RAG context alone is still useful.

        Why parallel?
            Jira and Slack are independent. Sequential calls would double latency.
            asyncio.gather() with return_exceptions=True means one failure doesn't
            block the other (per resilience_standards.md).
        """
        if self.mcp is None:
            return [], []

        try:
            results = await asyncio.gather(
                self.mcp.get("jira").search_tickets(query, project),
                self.mcp.get("slack").search_messages(query),
                return_exceptions=True,
            )
            jira_tickets   = results[0] if not isinstance(results[0], Exception) else []
            slack_messages = results[1] if not isinstance(results[1], Exception) else []

            logger.info(
                "CrossSourceAgent: MCP fetched %d Jira tickets, %d Slack messages",
                len(jira_tickets), len(slack_messages),
            )
            return jira_tickets, slack_messages

        except Exception:
            logger.exception("CrossSourceAgent: MCP fetch failed — degrading to RAG only")
            return [], []

    async def run(self, state: SDLCState) -> AgentPayload:
        """
        Execute cross-source correlation:
          1. RAG retrieval — historical docs from Qdrant
          2. MCP fetch   — live Jira + Slack data (parallel, optional)
          3. Build prompt — persona + RAG context + MCP live data + query
          4. Stream LLM response
          5. Return AgentPayload

        Why AgentPayload.structured carries the full response:
          summary is capped at 200 chars (for logging/monitoring).
          Full response lives in structured["final_response"] for the graph node.
        """
        query     = state["query"]
        project   = state["project_id"]
        user_role = state["user_role"]

        logger.info("CrossSourceAgent.run: query='%s...'", query[:60])

        # ── Step 1: RAG retrieval ─────────────────────────────────────────────
        chunks, confidence = self.retriever.retrieve(query, project)

        logger.info(
            "CrossSourceAgent: %d RAG chunks retrieved, top confidence=%.3f",
            len(chunks), confidence,
        )

        # ── Step 2: MCP live data ─────────────────────────────────────────────
        jira_tickets, slack_messages = await self._fetch_mcp_data(query, project)

        # ── Step 3: Build prompt ──────────────────────────────────────────────
        system_prompt = self.config.get_prompt("system_prompt")
        persona       = (
            self.config.get_prompt(f"persona_{user_role}")
            or self.config.get_prompt("persona_developer")
        )

        # Conversation history from retrieve_memory node (empty list on first turn)
        history_section = _format_conversation_history(state.get("recent_messages", []))
        rag_section     = _format_rag_context(chunks)
        mcp_section     = _format_mcp_context(jira_tickets, slack_messages)

        prompt = "\n\n".join(filter(None, [
            persona,
            history_section,    # prior turns — enables follow-up questions
            rag_section,
            mcp_section,
            f"## Current Question\n{query}",
        ]))

        # ── Step 4: Stream LLM response ───────────────────────────────────────
        temperature = self.config.get_temperature("response_generation")
        max_tokens  = (
            self.config.get_llm_config()
            .get("primary", {})
            .get("max_tokens", {})
            .get("response", 1024)
        )

        tokens: list[str] = []
        async for token in self.llm.generate(prompt, system_prompt, temperature, max_tokens):
            tokens.append(token)

        response = "".join(tokens) or "I don't have enough information to answer that confidently."

        logger.info("CrossSourceAgent: response %d chars", len(response))

        # ── Step 5: Package result ────────────────────────────────────────────
        all_sources = list({c.source for c in chunks})
        if jira_tickets:
            all_sources.append("jira_live")
        if slack_messages:
            all_sources.append("slack_live")

        return AgentPayload(
            agent_name="cross_source",
            confidence=confidence,
            summary=response[:200],
            structured={
                "final_response": response,
                "rag_chunks": [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in chunks
                ],
                "jira_tickets":   jira_tickets,
                "slack_messages": slack_messages,
            },
            sources=all_sources,
        )
