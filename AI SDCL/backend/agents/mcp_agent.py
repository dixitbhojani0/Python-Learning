"""
backend/agents/mcp_agent.py

MCPAgent — the gather-then-synthesize generalist (B7 Step 3).

The production replacement for the cross_source data-gathering blob:
  1. RAG retrieve (hybrid + corrective) over the knowledge base.
  2. GATHER live data over REAL MCP — the LLM selects + chains tools from their
     descriptions (backend/mcp_client/tool_use.gather_via_tools). No hardcoded
     `mcp.get("jira").search_tickets()`.
  3. SYNTHESIZE the answer with OUR pipeline (prompts.yaml + provider.generate),
     which then flows through the existing persona + faithfulness nodes.

Decoupling: tool *selection* (the MCP loop, provider-agnostic) is separate from
answer *authoring* (our generation). Live MCP data is placed before RAG so fresh
tool data wins over possibly-stale ingested chunks.

Operational note: this calls the MCP server over HTTP, so the SDLC MCP server
(`python -m backend.mcp_server.server`) must be running. In Docker it's its own
service. If the server is down, gather returns empty and we degrade to RAG-only.

Returns the same AgentPayload shape as the other agents, so persona, faithfulness,
the UI chips, and HITL all keep working unchanged.
"""
import logging
import re

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.core.metrics import faithfulness_score
from backend.core.prompt_safety import safety_guard
from backend.core.settings import settings
from backend.mcp_client.tool_use import gather_via_tools
from backend.memory.episodic_memory import episodic_memory
from backend.orchestrator.rag_helpers import format_rag_context, trim_rag_to_budget
from backend.orchestrator.state import SDLCState

logger = logging.getLogger(__name__)


def _classify_temporal_intent(query: str) -> str:
    """
    Classify whether a query asks about current state, past events, or both.
    Returns "current" | "historical" | "mixed".
    """
    q = query.lower()

    current_score = sum(1 for p in [
        r'\b(right now|currently|today|now|latest|active|current)\b',
        r'\b(show me|list|what are|which are|how many)\b',
        r'\b(in progress|open|ongoing|this sprint|this week)\b',
        r'\b(at the moment|as of now|status)\b',
    ] if re.search(p, q))

    historical_score = sum(1 for p in [
        r'\b(was|were|happened|caused|resolved|fixed|decided|introduced)\b',
        r'\b(last sprint|last week|previously|history|past|earlier|before|old)\b',
        r'\b(what caused|why did|how was|when did|in sprint \d)\b',
    ] if re.search(p, q))

    if current_score > historical_score:
        return "current"
    if historical_score > current_score:
        return "historical"
    return "mixed"


def _format_episodic_events(events: list[dict]) -> str:
    """
    Render recorded action-events (episodic memory) as a labelled history block.
    Used for historical/mixed queries so "how did we handle X?" can cite the
    actual sequence of approved actions, not just document context.
    """
    if not events:
        return ""
    lines = ["## Recorded Actions — Event History (most recent first)"]
    for e in events:
        when = (e.get("created_at", "") or "")[:10]
        actor = safety_guard.sanitize(e.get("actor", "") or "someone")
        text  = safety_guard.sanitize(e.get("text", ""))
        lines.append(f"- {when} — {actor}: {text}")
    return "\n".join(lines)


def _format_history(recent_messages: list[dict]) -> str:
    """Render recent conversation turns (from the memory node) for the prompt."""
    if not recent_messages:
        return ""
    lines = ["## Recent Conversation"]
    for m in recent_messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if len(lines) > 1 else ""


class MCPAgent(BaseAgent):
    """RAG + LLM-driven MCP tool-use, synthesized through the app's own generation."""

    def __init__(self, retriever, llm, config_loader=None, mcp_registry=None):
        # mcp_registry is accepted for a uniform constructor but unused: live data
        # comes via the MCP client (gather_via_tools), not the in-process registry.
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    async def _enrich_ticket_refs(self, chunks: list) -> str:
        """
        For every ticket ID found in RAG chunks, fetch its live Jira status + latest comment.
        Returns a formatted section that sits above the RAG content so the LLM sees
        authoritative live state before any (potentially stale) document claim.
        """
        import re
        import asyncio

        if not self.mcp or not self.mcp.has("jira"):
            return ""

        all_text   = " ".join(c.text for c in chunks)
        ticket_ids = list(dict.fromkeys(re.findall(r'\b[A-Z]{2,10}-\d{1,6}\b', all_text)))[:6]
        if not ticket_ids:
            return ""

        jira    = self.mcp.get("jira")
        results = await asyncio.gather(
            *[jira.get_ticket(tid) for tid in ticket_ids],
            return_exceptions=True,
        )

        lines = ["## Live Ticket Statuses (Authoritative — overrides document content)"]
        for tid, result in zip(ticket_ids, results):
            if isinstance(result, Exception) or not result:
                continue
            status   = result.get("status", "UNKNOWN")
            assignee = result.get("assignee", "unassigned")
            title    = result.get("title", "")
            comments = result.get("comments", [])
            latest   = ""
            if comments:
                c      = comments[-1]
                body   = (c.get("body") or "")[:180]
                latest = f"\n  Latest comment ({c.get('author', '?')}, {c.get('created', '?')}): {body}"
            lines.append(f"- **{tid}** [{status}] {title} — Assignee: {assignee}{latest}")

        return "\n".join(lines) if len(lines) > 1 else ""

    async def _rewrite_query(self, query: str) -> str:
        """LLM-based query reformulation for corrective RAG (same as the legacy agent)."""
        prompt = self.config.get_prompt("query_rewrite", query=query)
        if not prompt:
            return query
        try:
            resp = await self.llm.generate_text(
                prompt, self.config.get_prompt("system_prompt"), 0.1, 80
            )
            rewritten = resp.text.strip()
            return rewritten if rewritten else query
        except Exception:
            logger.exception("MCPAgent: _rewrite_query failed — using original")
            return query

    async def run(self, state: SDLCState) -> AgentPayload:
        query     = state["query"]
        project   = state.get("project_id", "") or settings.DEFAULT_PROJECT
        user_role = state.get("user_role", "developer")

        # ── 1. RAG (hybrid + corrective-RAG retry on low confidence) ──────────
        chunks, confidence, rag_strategy = await self.retriever.retrieve_with_corrective_rag(
            query, project, self._rewrite_query
        )
        logger.info("MCPAgent: %d RAG chunks, confidence=%.3f (%s)", len(chunks), confidence, rag_strategy)

        # ── 2. Live data via REAL MCP — the LLM picks/chains tools ────────────
        gathered = await gather_via_tools(query)
        logger.info("MCPAgent: MCP tools called: %s", gathered.tools_called or "none")

        # ── 2b. Enrich ticket IDs mentioned in RAG chunks with live Jira state ─
        # Prevents stale sprint-doc claims ("SDLC-1 is blocked") from overriding
        # the ticket's actual current status (e.g. DONE).
        live_ticket_section = await self._enrich_ticket_refs(chunks)
        if live_ticket_section:
            logger.info("MCPAgent: injected live ticket statuses for RAG-referenced tickets")

        # ── Domain guard: nothing in RAG and nothing from tools → out of domain ──
        if not chunks and gathered.is_empty:
            logger.warning("MCPAgent: no RAG and no MCP data — returning domain message")
            return AgentPayload(
                agent_name="mcp_agent",
                confidence=confidence,
                summary="Query out of domain — no SDLC context found",
                structured={
                    "final_response": (
                        "I couldn't find relevant information for that in the SDLC knowledge base or "
                        "live tools (Jira, GitHub, Slack, Confluence). Try a ticket ID, sprint, feature, "
                        "PR, or team member."
                    ),
                    "skip_persona": True,   # keep the refusal as-is — no persona re-framing
                },
                sources=[],
            )

        # ── Low-confidence guard: chunks exist but they're weak AND no live data ──
        # Without this, low-relevance chunks (e.g. release-notes mentioning
        # `all-MiniLM-L6-v2`) get synthesized for queries like "what is the LLM?",
        # producing a hedged paragraph that leaks internal infrastructure. Mirrors
        # BaseAgent._low_confidence_guard so all agents behave consistently.
        if confidence < BaseAgent._LOW_CONFIDENCE_THRESHOLD and gathered.is_empty:
            logger.warning(
                "MCPAgent: low confidence guard — conf=%.3f < %.2f, no MCP data — generic refusal",
                confidence, BaseAgent._LOW_CONFIDENCE_THRESHOLD,
            )
            return AgentPayload(
                agent_name="mcp_agent",
                confidence=confidence,
                summary="No high-confidence KB or MCP evidence",
                structured={
                    "final_response": (
                        "I don't have enough information to answer that confidently. "
                        "Please rephrase the question or include a specific feature, "
                        "ticket ID, document title, or team member."
                    ),
                    "skip_persona": True,   # persona must not reframe a clean refusal
                },
                sources=[],
            )

        # ── Classify temporal intent and check episodic memory ──
        temporal_intent = _classify_temporal_intent(query)
        logger.debug("MCPAgent: temporal_intent='%s' for query='%s...'", temporal_intent, query[:50])

        episodic_section = ""
        if temporal_intent in ("historical", "mixed"):
            try:
                raw_events = episodic_memory.search_events(query, project)
                if raw_events:
                    episodic_section = _format_episodic_events(raw_events)
                    logger.info("MCPAgent: retrieved and injected %d episodic events from memory", len(raw_events))
            except Exception:
                logger.exception("MCPAgent: episodic_memory lookup failed — continuing without events")

        # ── 3. Build the prompt (live MCP data BEFORE RAG so fresh data wins) ──
        system_prompt   = self.config.get_prompt("system_prompt")
        persona         = (
            self.config.get_prompt(f"persona_{user_role}")
            or self.config.get_prompt("persona_developer")
        )
        history_section = _format_history(state.get("recent_messages", []))
        mcp_section     = gathered.as_context()
        rag_section     = format_rag_context(chunks)
        safe_query      = safety_guard.safe_user_content(query)

        # Inject long-term semantic facts (B10 fix: was retrieved but never added to prompt)
        # Each fact is sanitized + XML-wrapped before injection (OWASP LLM07)
        _raw_facts = state.get("semantic_context", [])
        semantic_section = ""
        if _raw_facts:
            facts_text = "\n".join(
                f"- {safety_guard.safe_user_content(f)}" for f in _raw_facts
            )
            semantic_section = f"## Project Knowledge (from past conversations)\n{facts_text}"

        # Output-mode directive: the LLM decides whether to reproduce verbatim
        # or synthesise based on the query — no static keyword list (P1).
        # The system_prompt already instructs verbatim extraction for lists;
        # this section reinforces it AND explicitly allows persona to coexist.
        output_mode_directive = (
            "## Output guidance\n"
            "Read the query carefully and decide:\n"
            "- If the user wants specific document items (a checklist, steps, rules, "
            "guide, bullet list) → reproduce those items verbatim from the context; "
            "do NOT summarise into prose or add delivery framing.\n"
            "- If the user wants a conversational answer or analysis → use the persona "
            "style above and synthesise an answer.\n"
            "The system prompt and this directive take precedence over persona framing "
            "when the user is asking for literal content.\n"
            "**Data precedence (strictly enforced)**: "
            "Live Ticket Statuses section > Live Tool Data section > Document Context. "
            "A ticket listed as DONE or IN_PROGRESS in Live Ticket Statuses is NOT blocked "
            "— ignore any contradicting status claim in the document content below."
        )

        # I2: enforce token budget — trim RAG if the full prompt would overflow
        rag_section = trim_rag_to_budget(
            rag_section,
            [persona, output_mode_directive, semantic_section, history_section,
             mcp_section, live_ticket_section, episodic_section, safe_query],
        )

        prompt = "\n\n".join(filter(None, [
            persona,
            output_mode_directive,
            semantic_section,
            history_section,
            mcp_section,
            live_ticket_section,   # live status above RAG — stale doc claims can't win
            episodic_section,      # Inject episodic section here
            rag_section,
            f"## Current Question\n{safe_query}",
        ]))

        # ── 4. Synthesize the answer through OUR generation pipeline ──────────
        temperature = self.config.get_temperature("response_generation")
        _max_cfg    = self.config.get_llm_config().get("primary", {}).get("max_tokens", {})
        max_tokens  = (
            _max_cfg.get("response_full_document", 2400)
            if rag_strategy == "full_document"
            else _max_cfg.get("response", 1280)
        )

        tokens: list[str] = []
        async for token in self.llm.generate(prompt, system_prompt, temperature, max_tokens):
            tokens.append(token)
        response = self._guard_empty_llm("".join(tokens), query)

        # ── C1: Faithfulness gate — pure-RAG answers only ─────────────────────
        # Skip entirely when MCP tools ran: the answer intentionally mixes live Jira/
        # GitHub data with RAG chunks. The judge compares against RAG chunks only, so
        # MCP-sourced facts always score low — a guaranteed false positive.
        if chunks and response.strip() and gathered.is_empty:
            chunk_dicts = [{"text": c.text, "parent_text": c.parent_text} for c in chunks]
            faith = await faithfulness_score(query, response, chunk_dicts)
            logger.info("MCPAgent: faithfulness=%.3f", faith)
            if faith < 0.30:
                grounded_prompt = (
                    prompt
                    + "\n\nIMPORTANT: Your previous draft contained unverified claims. "
                    "Respond ONLY using facts explicitly stated in the retrieved context above."
                )
                retry_tokens: list[str] = []
                async for token in self.llm.generate(grounded_prompt, system_prompt, 0.0, max_tokens):
                    retry_tokens.append(token)
                retry_text = "".join(retry_tokens).strip()
                if retry_text:
                    retry_faith = await faithfulness_score(query, retry_text, chunk_dicts)
                    if retry_faith >= faith:
                        response = retry_text
                        faith = retry_faith
                # Don't prepend prefix when the LLM already states it can't answer —
                # adding "Based on limited context" before "I'm sorry, I don't have..."
                # is redundant and confusing.
                _SELF_AWARE_PREFIXES = ("i'm sorry", "i don't have", "i cannot", "i can't")
                if faith < 0.30 and not response.lower().lstrip().startswith(_SELF_AWARE_PREFIXES):
                    response = "Based on limited context — " + response

        logger.info("MCPAgent: response %d chars", len(response))

        # ── 5. Package result (same shape the other agents return) ────────────
        sources = list({c.source for c in chunks})
        # de-duped tool names as sources, e.g. "mcp:jira_get_blocked_tickets"
        for tool in dict.fromkeys(gathered.tools_called):
            sources.append(f"mcp:{tool}")

        return AgentPayload(
            agent_name="mcp_agent",
            confidence=confidence,
            summary=response[:200],
            structured={
                "final_response": response,
                "rag_strategy":   rag_strategy,
                "skip_persona":   False,   # LLM decides via output_mode_directive in prompt
                "rag_chunks": [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in chunks
                ],
                # MCP call trace — feeds the E9 "why this answer" panel later.
                "mcp_calls": [
                    {"tool": c.tool, "args": c.args, "error": c.error}
                    for c in gathered.calls
                ],
            },
            sources=sources,
        )
