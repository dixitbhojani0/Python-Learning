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
import re
from datetime import datetime, timezone

try:
    from langsmith import traceable
except ImportError:
    def traceable(fn=None, **_kw):
        return fn if fn is not None else (lambda f: f)

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.core.prompt_safety import safety_guard
from backend.memory.episodic_memory import episodic_memory
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever, RetrievedChunk, rerank_relevance

logger = logging.getLogger(__name__)


# ── Temporal intent classifier ────────────────────────────────────────────────

def _classify_temporal_intent(query: str) -> str:
    """
    Classify whether a query asks about current state, past events, or both.

    Returns "current" | "historical" | "mixed".

    This drives two things in the prompt:
      1. Which context section the LLM is told to treat as authoritative
      2. Whether MCP live data or RAG chunks are shown first

    Not hardcoded per query — the LLM handles all synthesis.
    We just give it the right signal so it reasons correctly.
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


# ── Document-recall intent ─────────────────────────────────────────────────────
# Detects "give me the WHOLE document/section" requests, which need full-document
# recall (all chunks reassembled in order) rather than top-k semantic retrieval.
# A "document noun" — when the user asks to SEE one of these, they want the whole thing.
_DOC_NOUNS = r'(checklist|guide|policy|policies|standards?|document|runbook|best\s+practices|guidelines?)'
_RECALL_INTENT = re.compile(
    # explicit "full/whole/all ..." anywhere
    r'\b(full|whole|entire|complete|all|every|each)\b.{0,30}' + _DOC_NOUNS
    + r'|\b(show|give|display|provide|see|read|get|open|view)\b.{0,40}\bthe\b.{0,30}' + _DOC_NOUNS
    + r'|\b(show|give|display|provide|see|read|get|open|view)\b.{0,40}' + _DOC_NOUNS
    + r'|\b(list|show)\s+(all|every|the)\b.{0,30}\b(checklist|items?|rules?|steps?|practices?|guidelines?)\b',
    re.IGNORECASE,
)


def _wants_full_document(query: str) -> bool:
    """True when the user is asking for an entire document/section, not a fact."""
    return bool(_RECALL_INTENT.search(query))


def _intent_note(temporal_intent: str) -> str:
    """
    Return a one-paragraph instruction for the LLM about how to prioritise sources.
    Placed just before the user question so it's the last thing the LLM reads.
    """
    if temporal_intent == "current":
        return (
            "**Data priority:** This query asks about CURRENT state. "
            "For ticket status, sprint state, and assignments use the Live Tool Data as the "
            "authoritative source. Historical chunks provide background context only. "
            "If they conflict, the live data wins."
        )
    if temporal_intent == "historical":
        return (
            "**Data priority:** This query asks about PAST events. "
            "Use the Retrieved Context as the primary source. "
            "Live Tool Data shows current state — mention it only if it adds relevant contrast."
        )
    return (
        "**Data priority:** This query spans both past and current data. "
        "Use Retrieved Context for historical events and Live Tool Data for current state. "
        "Clearly distinguish what was true then from what is true now."
    )


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


def _rag_freshness_note(chunks: list[RetrievedChunk]) -> str:
    """Extract the most recent ingested_date across all chunks for the freshness label."""
    dates = [
        c.metadata.get("ingested_date", "")
        for c in chunks
        if c.metadata.get("ingested_date")
    ]
    if not dates:
        return "historical reference"
    latest = max(dates)
    return f"ingested {latest}"


def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    """
    Format retrieved chunks into a labelled context block.
    Includes a freshness note so the LLM knows how old this data is
    and can weigh it correctly against live MCP data.
    """
    if not chunks:
        return ""
    # Image chunks carry OCR text that is mostly noisy screenshot fragments
    # ("togin()", mojibake). Feeding them to the LLM pollutes answers ("Image on
    # page 11 of ...") and bloats the prompt. Images are surfaced separately to the
    # UI via _images_from_chunks — they don't belong in the text context.
    text_chunks = [c for c in chunks if not (c.metadata or {}).get("is_image")]
    if not text_chunks:
        return ""
    freshness = _rag_freshness_note(text_chunks)
    lines = [f"## Retrieved Context — Historical Documents ({freshness})"]
    for i, chunk in enumerate(text_chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"\n### Source {i}: {chunk.source} ({chunk.doc_type})")
        lines.append(content)
    return "\n".join(lines)


_MAX_RESPONSE_IMAGES = 6  # cap so an image-heavy doc can't flood the chat


def _images_from_chunks(
    chunks: list[RetrievedChunk],
    query: str = "",
    cap: int = _MAX_RESPONSE_IMAGES,
    min_relevance: float = 0.20,
) -> list[dict]:
    """
    Collect image references from image chunks (is_image=True), keeping only the
    ones actually relevant to the query and capping the count.

    Two problems this guards against:
      • a status/risk query that happens to retrieve a stray code-screenshot image
        would otherwise attach an irrelevant picture, and
      • an image-heavy document (e.g. a 30-page slide deck) would flood the chat
        with dozens of pictures.

    When `query` is given, each image's OCR caption is reranked against the query
    and dropped below `min_relevance`. When `query` is empty (explicit full-document
    recall), the relevance filter is skipped but the cap still applies.
    Deduplicated by image_path; highest-ranked first.
    """
    seen: set[str] = set()
    scored: list[tuple[float, dict]] = []
    for c in chunks:
        md = c.metadata or {}
        if not md.get("is_image"):
            continue
        path = md.get("image_path", "")
        if not path or path in seen:
            continue
        seen.add(path)
        caption = c.text.strip()
        # Relevance gate (skipped for full-document recall where query="")
        if query and caption:
            if rerank_relevance(query, [caption]) < min_relevance:
                continue
        scored.append((c.score, {
            "url":       f"/{path.lstrip('/')}",          # "images/x.png" -> "/images/x.png"
            "doc_title": md.get("doc_title", "") or md.get("section_title", ""),
            "page":      md.get("page_number"),
            "caption":   caption[:200] if md.get("has_ocr") else "",
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [img for _, img in scored[:cap]]


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


def _format_chat_messages(messages: list[dict]) -> list[str]:
    """Render a list of chat messages (Slack or Teams) as sanitized bullet lines."""
    lines = []
    for m in messages:
        safe_user = safety_guard.sanitize(m.get("user", "?"))
        safe_msg  = safety_guard.sanitize(m.get("message", ""))
        lines.append(f"- {safe_user} ({m.get('timestamp', '')[:10]}): {safe_msg}")
    return lines


def _format_mcp_context(
    jira_tickets: list[dict],
    slack_messages: list[dict],
    teams_messages: list[dict] | None = None,
) -> str:
    """
    Format live MCP tool data into a labelled section.
    Includes a fetch timestamp so the LLM knows this is authoritative for current state.
    """
    teams_messages = teams_messages or []
    if not jira_tickets and not slack_messages and not teams_messages:
        return ""

    fetch_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"## Live Tool Data — Current State (fetched {fetch_time})"]

    if jira_tickets:
        lines.append("\n### Jira: Current Tickets")
        for t in jira_tickets:
            status        = t.get("status", "UNKNOWN")
            raw_blockers  = "; ".join(t.get("blockers", [])) or "none"
            safe_title    = safety_guard.sanitize(t.get("title", ""))
            safe_assignee = safety_guard.sanitize(t.get("assignee", "unassigned"))
            safe_blockers = safety_guard.sanitize(raw_blockers)
            lines.append(
                f"- [{t['id']}] [{status}] {safe_title}"
                f" (assignee: {safe_assignee}, blockers: {safe_blockers})"
            )

    if slack_messages:
        lines.append("\n### Slack: Recent Messages")
        lines.extend(_format_chat_messages(slack_messages))

    if teams_messages:
        lines.append("\n### Microsoft Teams: Recent Messages")
        lines.extend(_format_chat_messages(teams_messages))

    return "\n".join(lines)


# ── Ticket suggestion helpers ─────────────────────────────────────────────────

def _format_existing_tickets(jira_tickets: list[dict]) -> str:
    if not jira_tickets:
        return "None found."
    return "\n".join(
        f"- [{t['id']}] [{t.get('status','?')}] {t.get('title','')}"
        for t in jira_tickets
    )


def _format_ticket_suggestion_card(proposal: dict, investigation: str) -> str:
    """
    Combine the investigation answer with a ticket creation proposal card.
    The investigation answer is shown first so the user sees the full context,
    then the HITL card asks whether to file a ticket for the discovered issue.
    """
    title       = proposal.get("title", "")
    description = proposal.get("description", "")
    priority    = proposal.get("priority", "MEDIUM")
    labels      = proposal.get("labels", [])
    label_str   = f"\n🏷️ **Labels:** {', '.join(labels)}" if labels else ""

    return "\n\n".join([
        investigation,
        "---",
        "## 🎫 Untracked Issue Detected",
        (
            "The issue above does not appear to have a Jira ticket yet. "
            "Would you like me to create one?\n\n"
            f"📋 **Title:** {title}\n"
            f"📝 **Description:** {description}\n"
            f"📊 **Priority:** {priority}"
            f"{label_str}\n\n"
            "_Click **Approve** to create this ticket in Jira, or **Reject** to skip._"
        ),
    ])


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

    async def _rewrite_query(self, query: str) -> str:
        """LLM-based query reformulation for corrective RAG."""
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
            logger.exception("CrossSourceAgent: _rewrite_query failed — using original")
            return query

    async def _fetch_mcp_data(
        self, query: str, project: str
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """
        Fetch live Jira tickets + Slack messages + Teams messages in parallel.

        Returns (jira_tickets, slack_messages, teams_messages).
        If MCP registry is not wired or a connector fails, returns empty lists.
        Teams is OPTIONAL — only queried when the connector is registered, so a
        disabled Teams connector never breaks the proven Jira+Slack path.
        The agent degrades gracefully — RAG context alone is still useful.

        Why parallel?
            The sources are independent. Sequential calls would multiply latency.
            asyncio.gather(return_exceptions=True) means one failure doesn't
            block the others (per resilience_standards.md).
        """
        if self.mcp is None:
            return [], [], []

        try:
            has_teams = self.mcp.has("teams")
            tasks = [
                self.mcp.get("jira").search_tickets(query, project),
                self.mcp.get("slack").search_messages(query),
            ]
            if has_teams:
                tasks.append(self.mcp.get("teams").search_messages(query))

            results        = await asyncio.gather(*tasks, return_exceptions=True)
            jira_tickets   = results[0] if not isinstance(results[0], Exception) else []
            slack_messages = results[1] if not isinstance(results[1], Exception) else []
            teams_messages = (
                results[2] if has_teams and not isinstance(results[2], Exception) else []
            )

            logger.info(
                "CrossSourceAgent: MCP fetched %d Jira tickets, %d Slack, %d Teams messages",
                len(jira_tickets), len(slack_messages), len(teams_messages),
            )
            return jira_tickets, slack_messages, teams_messages

        except Exception:
            logger.exception("CrossSourceAgent: MCP fetch failed — degrading to RAG only")
            return [], [], []

    async def _check_ticket_needed(
        self,
        query: str,
        response: str,
        jira_tickets: list[dict],
        project: str,
    ) -> dict | None:
        """
        Secondary LLM call: decide whether the investigation found an untracked issue
        that warrants a new Jira ticket. Returns a proposal dict or None.
        """
        prompt = self.config.get_prompt(
            "cross_source_ticket_suggestion",
            query=query,
            response_summary=response[:600],
            existing_tickets=_format_existing_tickets(jira_tickets),
        )
        if not prompt:
            return None

        try:
            resp = await self.llm.generate_structured(
                prompt,
                self.config.get_prompt("system_prompt"),
                temperature=0.0,
                max_tokens=700,
            )
            if resp.parse_error or not resp.structured:
                return None

            data = resp.structured
            if not data.get("should_create"):
                logger.warning("CrossSourceAgent: ticket suggestion skipped — %s", data.get("reason", ""))
                return None

            logger.warning("CrossSourceAgent: ticket suggestion — recommends creating: %s", data.get("title", ""))
            return {
                "action":      "create_ticket",
                "title":       data.get("title", ""),
                "description": data.get("description", ""),
                "priority":    data.get("priority", "MEDIUM"),
                "assignee":    "unassigned",
                "labels":      data.get("labels", []),
                "project":     project,
            }
        except Exception:
            logger.exception("CrossSourceAgent: _check_ticket_needed failed — skipping suggestion")
            return None

    @traceable(name="cross_source_agent", run_type="chain")
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

        # ── Classify temporal intent upfront — drives context ordering + LLM hint
        temporal_intent = _classify_temporal_intent(query)
        logger.debug("CrossSourceAgent: temporal_intent='%s' for query='%s...'", temporal_intent, query[:50])

        # ── Step 1: RAG retrieval ─────────────────────────────────────────────
        # Route by query type (agentic):
        #   • "show me the WHOLE checklist" → document RECALL: identify the target
        #     doc via a cheap semantic probe, then pull ALL its chunks in order.
        #   • everything else → semantic top-k with corrective-RAG loop.
        if _wants_full_document(query):
            doc_title = self.retriever.identify_document(query, project)
            if doc_title:
                chunks      = self.retriever.retrieve_full_document(doc_title, project)
                confidence  = chunks[0].score if chunks else 0.0
                rag_strategy = "full_document"
                logger.info(
                    "CrossSourceAgent: RECALL mode — doc='%s' → %d chunks",
                    doc_title, len(chunks),
                )
            else:
                chunks, confidence, rag_strategy = await self.retriever.retrieve_with_corrective_rag(
                    query, project, self._rewrite_query
                )
        else:
            # If first-pass confidence is low (<0.45), the LLM rewrites the query
            # and retrieval is retried once before falling back to whatever was found.
            chunks, confidence, rag_strategy = await self.retriever.retrieve_with_corrective_rag(
                query, project, self._rewrite_query
            )

        logger.info(
            "CrossSourceAgent: %d RAG chunks retrieved, top confidence=%.3f, strategy=%s",
            len(chunks), confidence, rag_strategy,
        )

        # ── Hallucination guard: abort if RAG confidence is too low ──────────
        # Threshold 0.20 — if RAG is below this, MCP live data can still rescue
        # the query (e.g. live Jira returns ticket data with no historical chunks).
        low_conf_payload = self._low_confidence_guard(confidence, chunks, query, threshold=0.20)

        # ── Step 2: MCP live data ─────────────────────────────────────────────
        jira_tickets, slack_messages, teams_messages = await self._fetch_mcp_data(query, project)

        # ── Agentic relevance gate ────────────────────────────────────────────
        # Score the live MCP data against the query with the reranker (the same
        # ML model used for RAG) instead of trusting raw keyword hits. If the best
        # MCP snippet is below the no-evidence threshold, the data is noise for
        # THIS query — drop it so it neither pollutes the prompt nor masks an
        # out-of-domain query. This is what makes the in-domain decision agentic.
        mcp_snippets = (
            [f"{t.get('id','')} {t.get('title','')} {t.get('status','')}" for t in jira_tickets]
            + [m.get("message", "") for m in slack_messages]
            + [m.get("message", "") for m in teams_messages]
        )
        if mcp_snippets:
            threshold     = self.config.get_confidence_thresholds().get("no_evidence_threshold", 0.20)
            mcp_relevance = rerank_relevance(query, mcp_snippets)
            if mcp_relevance < threshold:
                logger.info(
                    "CrossSourceAgent: MCP data relevance %.3f < %.3f — dropping as off-topic",
                    mcp_relevance, threshold,
                )
                jira_tickets, slack_messages, teams_messages = [], [], []

        # BOTH RAG and MCP found nothing relevant → query is out of this system's domain.
        if low_conf_payload is not None and not jira_tickets and not slack_messages and not teams_messages:
            logger.warning("CrossSourceAgent: no RAG chunks and no MCP data — returning domain message")
            domain_message = (
                "I couldn't find relevant information for that question in the SDLC knowledge base.\n\n"
                "I can help with:\n"
                "- **Sprint status** — goals, velocity, blockers, what's in progress\n"
                "- **Jira tickets** — status, assignee, priority (e.g. *\"What's the status of SDLC-3?\"*)\n"
                "- **GitHub PRs** — open PRs, CI status, reviewer assignment, reviews\n"
                "- **Incidents & ADRs** — past outages, architecture decisions, root causes\n"
                "- **Release readiness** — go/no-go, what's blocking the release\n"
                "- **Team activity** — Slack discussions, who's working on what\n\n"
                "Try asking with a ticket ID, PR number, feature name, or team member name."
            )
            return AgentPayload(
                agent_name="cross_source",
                confidence=confidence,
                summary="Query out of domain — no SDLC context found",
                # Persona runs (3 roles stay meaningful), but the persona prompts now
                # forbid inventing status/metrics, so this stays a brief role-appropriate
                # "not found" reply rather than a fabricated sprint report.
                structured={"final_response": domain_message},
                sources=[],
                hitl_required=False,
                hitl_proposal={},
            )

        # ── Step 3: Build prompt ──────────────────────────────────────────────
        system_prompt = self.config.get_prompt("system_prompt")
        persona       = (
            self.config.get_prompt(f"persona_{user_role}")
            or self.config.get_prompt("persona_developer")
        )

        # Conversation history from retrieve_memory node (empty list on first turn)
        history_section = _format_conversation_history(state.get("recent_messages", []))
        rag_section     = _format_rag_context(chunks)
        mcp_section     = _format_mcp_context(jira_tickets, slack_messages, teams_messages)

        # Episodic memory: for past-looking queries, surface the recorded sequence
        # of approved actions (ticket created → reviewer assigned → release approved).
        # Empty until HITL actions have been approved; filtered out when empty.
        episodic_section = ""
        if temporal_intent in ("historical", "mixed"):
            episodic_section = _format_episodic_events(
                episodic_memory.search_events(query, project)
            )

        safe_query = safety_guard.safe_user_content(query)

        # For "current" intent: put live MCP data before RAG so LLM sees it first.
        # For "historical": RAG + recorded events lead. For "mixed": RAG then MCP.
        if temporal_intent == "current":
            context_sections = [mcp_section, rag_section]
        else:
            context_sections = [rag_section, episodic_section, mcp_section]

        prompt = "\n\n".join(filter(None, [
            persona,
            history_section,
            *context_sections,
            _intent_note(temporal_intent),
            f"## Current Question\n{safe_query}",
        ]))

        # ── Step 4: Stream LLM response ───────────────────────────────────────
        temperature = self.config.get_temperature("response_generation")
        # Dynamic length: full-document recall (checklists/policies) needs room to
        # reproduce the whole thing; normal status answers stay tight to cut noise.
        _max_tokens_cfg = self.config.get_llm_config().get("primary", {}).get("max_tokens", {})
        if rag_strategy == "full_document":
            max_tokens = _max_tokens_cfg.get("response_full_document", 2400)
        else:
            max_tokens = _max_tokens_cfg.get("response", 1280)

        tokens: list[str] = []
        async for token in self.llm.generate(prompt, system_prompt, temperature, max_tokens):
            tokens.append(token)

        response_raw = "".join(tokens)
        response     = self._guard_empty_llm(response_raw, query)

        # If the LLM returned a real answer but it's the generic fallback string,
        # keep it — it's better than the empty-output message.
        if not response_raw.strip():
            response = response   # already the empty-output message from guard
        elif response_raw.strip() == "I don't have enough information to answer that confidently.":
            pass  # fine — the model is being honest

        logger.info("CrossSourceAgent: response %d chars", len(response))

        # ── Step 5: Package result ────────────────────────────────────────────
        all_sources = list({c.source for c in chunks})
        if jira_tickets:
            all_sources.append("jira_live")
        if slack_messages:
            all_sources.append("slack_live")
        if teams_messages:
            all_sources.append("teams_live")

        # ── Step 6 (optional): Suggest ticket creation via HITL ───────────────
        # Runs when:
        #   - Confidence is reasonable (real issue found in context, >= 0.20)
        #   - Query language signals an unresolved problem (error/bug/slow/failing/broken)
        #   - NOT a resolved/historical question ("how was X fixed", "what caused Y")
        # The secondary LLM call compares the discovered issue against existing tickets
        # and only returns should_create=True when none cover this specific problem.
        _PROBLEM_SIGNALS = re.compile(
            r'\b(error|bug|slow|failing|broken|not working|crash|issue|problem|outage|'
            r'timeout|leak|missing|can\'t|cannot|doesn\'t|fails|500|404)\b',
            re.IGNORECASE,
        )
        _RESOLVED_SIGNALS = re.compile(
            r'\b(how was|how did|was resolved|was fixed|was closed|in the past|last sprint|'
            r'previously|history|what caused)\b',
            re.IGNORECASE,
        )
        _suggest_ticket = (
            bool(_PROBLEM_SIGNALS.search(query))
            and not bool(_RESOLVED_SIGNALS.search(query))
        )

        hitl_required  = False
        hitl_proposal: dict = {}
        final_response = response

        if _suggest_ticket:
            ticket_suggestion = await self._check_ticket_needed(
                query, response, jira_tickets, project,
            )
            if ticket_suggestion:
                hitl_required  = True
                hitl_proposal  = ticket_suggestion
                ticket_card    = _format_ticket_suggestion_card(ticket_suggestion, response)
                final_response = ticket_card

        return AgentPayload(
            agent_name="cross_source",
            confidence=confidence,
            summary=response[:200],
            structured={
                "final_response": final_response,
                "rag_strategy":  rag_strategy,
                "rag_chunks": [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in chunks
                ],
                # Full-document recall = user explicitly asked for the doc, so skip the
                # relevance filter (query=""); otherwise filter images by query relevance.
                "images":         _images_from_chunks(
                    chunks, query="" if rag_strategy == "full_document" else query,
                ),
                "jira_tickets":   jira_tickets,
                "slack_messages": slack_messages,
                "teams_messages": teams_messages,
            },
            sources=all_sources,
            hitl_required=hitl_required,
            hitl_proposal=hitl_proposal,
        )
