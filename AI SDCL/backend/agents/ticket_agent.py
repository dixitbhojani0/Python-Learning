"""
backend/agents/ticket_agent.py

Ticket Creation + Assignment Agent — HITL for both creating and assigning tickets.

Creation flow:
  1. Search Jira for similar tickets (dedup)
  2. RAG — past incidents + sprint context
  3. LLM CoT → structured ticket fields (JSON)
  4. Format HITL proposal card
  5. Return AgentPayload(hitl_required=True)

Assignment flow (triggered by "assign SDLC-4"):
  1. Fetch ticket + project members in parallel
  2. Suggest best assignee (explicit name from query or first active member)
  3. Return HITL proposal card with team roster
"""
import asyncio
import logging
import re
from collections import Counter

try:
    from langsmith import traceable
except ImportError:
    def traceable(fn=None, **_kw):
        return fn if fn is not None else (lambda f: f)

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.mcp_client.client import call_mcp_tool
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


# ── Context formatters ─────────────────────────────────────────────────────────

def _format_similar_tickets(tickets: list[dict]) -> str:
    if not tickets:
        return "No similar existing tickets found in Jira."
    lines = []
    for t in tickets:
        blockers = ", ".join(t.get("blockers", [])) or "none"
        lines.append(
            f"- [{t['id']}] [{t['status']}] [{t.get('priority', 'MEDIUM')}] {t['title']}\n"
            f"  Assignee: {t.get('assignee', 'unassigned')} | Blockers: {blockers}\n"
            f"  Description: {t.get('description', '')[:200]}"
        )
    return "\n\n".join(lines)


def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "No historical context found."
    lines = []
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"[Source {i}: {chunk.source} ({chunk.doc_type})]")
        lines.append(content[:600])
    return "\n\n".join(lines)


# ── Proposal card formatter ────────────────────────────────────────────────────

def _format_ticket_proposal(ticket_data: dict, similar_tickets: list[dict], project_members: list[dict] | None = None) -> str:
    """
    Build the human-readable proposal card shown in Chainlit before HITL approval.
    """
    similar_count = ticket_data.get("similar_count", len(similar_tickets))
    past_ref      = ticket_data.get("similar_ticket_ref", "")
    resolution    = ticket_data.get("similar_ticket_resolution", "")
    labels        = ticket_data.get("labels", [])
    assignee      = ticket_data.get("assignee", "unassigned")

    # Build assignee display line — include team roster so user can see alternatives
    if project_members:
        member_names = ", ".join(m["display_name"] or m["name"] for m in project_members)
        assignee_line = f"👤 **Suggested Assignee:** {assignee}  _(team: {member_names})_"
    else:
        assignee_line = f"👤 **Suggested Assignee:** {assignee}"

    # P0→Critical, P1→High, P2→Medium, P3→Low — human-readable for the proposal card
    _PRIORITY_LABEL = {"P0": "Critical", "P1": "High", "P2": "Medium", "P3": "Low",
                       "CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low",
                       "HIGHEST": "Critical", "LOWEST": "Low"}
    raw_priority    = ticket_data.get("priority", "MEDIUM").upper()
    priority_label  = _PRIORITY_LABEL.get(raw_priority, raw_priority.capitalize())

    header = (
        f"Based on the issue described and **{similar_count}** similar past incident(s), "
        f"I suggest creating:"
        if similar_count > 0
        else "I suggest creating the following ticket:"
    )

    lines = [
        header,
        "",
        f"📋 **Title:** {ticket_data.get('title', 'N/A')}",
        f"📝 **Description:** {ticket_data.get('description', 'N/A')}",
        assignee_line,
        f"📊 **Priority:** {priority_label}",
    ]

    if labels:
        lines.append(f"🏷️ **Labels:** {', '.join(labels)}")

    if past_ref:
        lines += [
            "",
            f"> ⚠️ **Similar ticket already exists: {past_ref}** — review before creating a duplicate.",
            f"> Resolution used: _{resolution}_" if resolution else "",
        ]

    lines += [
        "",
        "---",
        "_Click **Approve** to create this ticket in Jira, or **Reject** to cancel._",
        "_To assign to a different team member, reject and re-ask: \"create ticket and assign to [name]\"._",
    ]
    return "\n".join(lines)


def _clean_ticket_query(query: str) -> str:
    """
    Preprocess/clean ticket creation query to extract the core subject of the issue.
    This strips out common ticket creation verbs and patterns, returning the core
    nouns/keywords to be used for duplicate checking and similar ticket search.
    """
    # Normalize spaces
    q = " ".join(query.split())
    # Match prefixes like "create ticket regarding...", "create a ticket for..."
    pattern = r'(?i)^\b(create|raise|file|report|open)\s+(a\s+|an\s+|one\s+|new\s+)?(ticket|bug|issue|story|task|defect)\s+(regarding|for|about|with|on)?\s*'
    q_cleaned = re.sub(pattern, '', q)

    # If the query starts with "regarding", "for", "about", strip it too
    q_cleaned = re.sub(r'(?i)^\b(regarding|for|about|with|on)\s+', '', q_cleaned)

    # Strip trailing punctuation
    q_cleaned = q_cleaned.rstrip('.!? ')
    return q_cleaned


# ── Agent class ────────────────────────────────────────────────────────────────

class TicketAgent(BaseAgent):
    """
    Ticket Creation Agent — proposes a Jira ticket with human approval.

    1. Jira MCP — search for similar/duplicate tickets
    2. RAG — retrieve past incident reports and sprint docs
    3. LLM CoT → structured JSON ticket fields
    4. Format HITL proposal card
    5. Return AgentPayload(hitl_required=True)
    """

    def __init__(self, retriever: HybridRetriever, llm, config_loader=None, mcp_registry=None):
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    async def _fetch_similar_tickets(self, query: str, project: str) -> list[dict]:
        """Search Jira (over MCP) for tickets similar to the reported issue."""
        cleaned = _clean_ticket_query(query)
        logger.info("TicketAgent: searching similar tickets for cleaned query: %r (original: %r)", cleaned, query)
        try:
            tickets = await call_mcp_tool("jira_search_tickets", {"query": cleaned, "project": project})
            if not isinstance(tickets, list):
                tickets = []
            logger.info("TicketAgent: found %d similar Jira tickets", len(tickets))
            return tickets
        except Exception:
            logger.exception("TicketAgent: Jira search failed — proceeding without similar tickets")
            return []

    async def _fetch_project_members(self, project: str) -> list[dict]:
        """Fetch all assignable members for the project from Jira (over MCP)."""
        try:
            members = await call_mcp_tool("jira_get_project_members", {"project": project})
            return members if isinstance(members, list) else []
        except Exception:
            logger.warning("TicketAgent: could not fetch project members — assignee will be unassigned")
            return []

    @staticmethod
    def _suggest_assignee_member(members: list[dict], similar_tickets: list[dict]) -> dict | None:
        """
        Pick the best assignee from project members. Returns the full member dict
        so callers can use both display_name (for UI) and account_id (for Jira API).

        Priority:
          1. Member who owns the most similar tickets (component ownership signal)
          2. First active member alphabetically (safe fallback)
        """
        if not members:
            return None

        # Check similar tickets for ownership signal — match by display_name or name
        if similar_tickets:
            owner_counts: Counter = Counter()
            for t in similar_tickets:
                owner = t.get("assignee", "")
                if owner and owner != "unassigned":
                    owner_counts[owner] += 1
            if owner_counts:
                best_owner = owner_counts.most_common(1)[0][0].lower()
                for m in members:
                    combined = ((m.get("display_name") or "") + " " + (m.get("name") or "")).lower()
                    if best_owner in combined:
                        return m

        # Fall back to first active member alphabetically by display_name
        active = sorted(
            [m for m in members if m.get("active", True)],
            key=lambda m: (m.get("display_name") or m.get("name", "")).lower(),
        )
        return active[0] if active else None

    async def _run_assignment(self, state: SDLCState, ticket_id: str) -> AgentPayload:
        """Handle assign/reassign of an existing Jira ticket with HITL."""
        project = state["project_id"]
        query   = state["query"]

        results = await asyncio.gather(
            call_mcp_tool("jira_get_ticket", {"ticket_id": ticket_id}),
            call_mcp_tool("jira_get_project_members", {"project": project}),
            return_exceptions=True,
        )
        ticket  = results[0] if isinstance(results[0], dict) else {}
        members = results[1] if isinstance(results[1], list) else []

        # If ticket is empty, it doesn't exist in Jira — reject immediately, no HITL
        if not ticket or not ticket.get("title"):
            return AgentPayload(
                agent_name="ticket_agent",
                confidence=1.0,
                summary=f"Ticket {ticket_id} not found",
                structured={"final_response": f"Ticket **{ticket_id}** does not exist in Jira project `{project}`. Please check the ticket ID and try again."},
                sources=["jira_live"],
                hitl_required=False,
                hitl_proposal={},
                response=f"Ticket **{ticket_id}** does not exist in Jira project `{project}`.",
            )

        ticket_title     = ticket.get("title", ticket_id)
        ticket_status    = ticket.get("status", "UNKNOWN")
        current_assignee = ticket.get("assignee", "unassigned")

        # Check if user named a specific assignee: "assign SDLC-4 to alice"
        to_match     = re.search(r'\bto\s+(\w+)', query.lower())
        named_target = to_match.group(1) if to_match else None

        suggested_member = None
        if members:
            if named_target:
                for m in members:
                    combined = ((m.get("name") or "") + " " + (m.get("display_name") or "")).lower()
                    if named_target in combined:
                        suggested_member = m
                        break
                if not suggested_member:
                    # Named target not found — do NOT silently substitute another person.
                    # Return a clarification response instead of assigning the wrong user.
                    member_list_str = ", ".join(
                        (m.get("display_name") or m.get("name", "?")) for m in members
                    ) or "no members found"
                    clarification = (
                        f"I couldn't find a team member named **'{named_target}'** in Jira "
                        f"for project `{project}`.\n\n"
                        f"**Available team members:** {member_list_str}\n\n"
                        f"Please try again with one of the names above, "
                        f"or specify the full display name."
                    )
                    return AgentPayload(
                        agent_name="ticket_agent",
                        confidence=0.9,
                        summary=f"Assignee '{named_target}' not found in project",
                        structured={"final_response": clarification},
                        sources=["jira_live"],
                        hitl_required=False,
                        hitl_proposal={},
                    )
            else:
                suggested_member = members[0] if members else None

        suggested_name    = (suggested_member or {}).get("display_name") or (suggested_member or {}).get("name", "unassigned")
        suggested_acct_id = (suggested_member or {}).get("account_id", "")
        member_list       = ", ".join((m.get("display_name") or m.get("name", "?")) for m in members) if members else "no members found"

        # Scenario: already assigned to the same person — no action needed
        if current_assignee.lower() not in ("unassigned", "") and suggested_name.lower() in current_assignee.lower():
            already_msg = (
                f"Ticket **{ticket_id}** is already assigned to **{current_assignee}**. "
                f"No action needed."
            )
            return AgentPayload(
                agent_name="ticket_agent",
                confidence=1.0,
                summary=f"{ticket_id} already assigned to {current_assignee}",
                structured={"final_response": already_msg},
                sources=["jira_live"],
                hitl_required=False,
                hitl_proposal={},
                response=already_msg,
            )

        # Scenario: reassign from one person to another — make it explicit in the card
        is_reassign  = current_assignee not in ("unassigned", "")
        action_label = f"reassign from **{current_assignee}** to **{suggested_name}**" if is_reassign else f"assign to **{suggested_name}**"

        proposal_card = "\n".join([
            f"## {'Reassign' if is_reassign else 'Assign'} Ticket {ticket_id}",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Ticket | **{ticket_id}** — {ticket_title} |",
            f"| Status | {ticket_status} |",
            f"| Current Assignee | {current_assignee} |",
            f"| New Assignee | **{suggested_name}** |",
            f"| Available Team | {member_list} |",
            "",
            f"_Click **Approve** to {action_label}, or **Reject** to cancel._",
        ])

        return AgentPayload(
            agent_name="ticket_agent",
            confidence=0.9,
            summary=f"Assign proposal: {ticket_id} → {suggested_name}",
            structured={"final_response": proposal_card},
            sources=["jira_live"],
            hitl_required=True,
            hitl_proposal={
                "action":     "assign_ticket",
                "ticket_id":  ticket_id,
                "account_id": suggested_acct_id,
                "assignee":   suggested_name,
                "project":    project,
            },
        )

    async def _run_comment(self, state: SDLCState, ticket_id: str) -> AgentPayload:
        """Propose adding a developer comment to a ticket (E6) — HITL gated."""
        query   = state["query"]
        project = state["project_id"]

        # Extract the comment body: text after a colon / "saying" / "that".
        body_match   = re.search(r'(?::|\bsaying\b|\bthat\b)\s+(.+)$', query, re.IGNORECASE)
        comment_body = body_match.group(1).strip() if body_match else ""

        if not comment_body:
            msg = (
                f"What comment should I add to **{ticket_id}**? For example:\n\n"
                f"`add a comment to {ticket_id}: investigated — this is a 3-day refactor, not a quick fix.`"
            )
            return AgentPayload(
                agent_name="ticket_agent", confidence=1.0,
                summary="comment body needed",
                structured={"final_response": msg}, sources=[], response=msg,
            )

        card = "\n".join([
            f"## Add Comment to {ticket_id}",
            "",
            f"> {comment_body}",
            "",
            f"_Click **Approve** to post this comment to {ticket_id}, or **Reject** to cancel._",
        ])
        return AgentPayload(
            agent_name="ticket_agent", confidence=0.9,
            summary=f"Comment proposal for {ticket_id}",
            structured={"final_response": card},
            sources=["jira_live"],
            hitl_required=True,
            hitl_proposal={
                "action":    "comment_ticket",
                "ticket_id": ticket_id,
                "comment":   comment_body,
                "project":   project,
            },
        )

    @traceable(name="ticket_agent", run_type="chain")
    async def run(self, state: SDLCState) -> AgentPayload:
        """Propose a Jira ticket for the reported issue — always requires HITL."""
        query   = state["query"]
        project = state["project_id"]

        logger.info("TicketAgent.run: project='%s' query='%s...'", project, query[:60])

        # ── Assignment intent: "assign SDLC-4" / "reassign SDLC-2 to alice" ──
        # Also handles common variations: "sdlc 5" (space), "sdlc5" (no separator), lowercase
        assign_match    = re.search(r'\b(assign|reassign)\b', query.lower())
        ticket_id_match = re.search(r'\b([A-Za-z]{2,10})[- ]?(\d+)\b', query)
        if assign_match and ticket_id_match:
            ticket_id = f"{ticket_id_match.group(1).upper()}-{ticket_id_match.group(2)}"
            return await self._run_assignment(state, ticket_id)

        # ── Comment intent (E6): "add a comment to SDLC-5: ...", "log a note on SDLC-5 saying ..." ──
        # Requires an explicit write verb so it doesn't fire on "show comments on SDLC-5" (a read).
        comment_match = re.search(r'\b(add|log|leave|post|write)\s+(a\s+)?(comment|note)\b', query.lower())
        if comment_match and ticket_id_match:
            ticket_id = f"{ticket_id_match.group(1).upper()}-{ticket_id_match.group(2)}"
            return await self._run_comment(state, ticket_id)

        # ── List/search intent: "show open tickets", "list tickets for X", "find bugs", "who is assigned" ──
        # These are read-only queries — search Jira and return results without HITL.
        _LIST_WORDS   = re.compile(r'\b(show|list|find|search|get|display|what|which|who)\b', re.I)
        _CREATE_WORDS = re.compile(r'\b(create|raise|file|report|open a( new)? (ticket|issue|bug))\b', re.I)
        if _LIST_WORDS.search(query) and not _CREATE_WORDS.search(query):
            tickets = await self._fetch_similar_tickets(query, project)
            if tickets:
                lines = [f"Here are the Jira tickets I found in **{project}**:\n"]
                for t in tickets:
                    assignee = t.get("assignee") or "unassigned"
                    lines.append(
                        f"- **{t['id']}** [{t.get('status', '?')}] [{t.get('priority', '?')}] "
                        f"{t['title']} — Assignee: {assignee}"
                    )
                response_text = "\n".join(lines)
            else:
                response_text = f"No tickets found matching your search in project **{project}**."
            return AgentPayload(
                agent_name="ticket_agent",
                confidence=0.9,
                summary="Jira ticket search results",
                structured={"final_response": response_text},
                sources=["jira_live"],
                hitl_required=False,
                hitl_proposal={},
            )

        # ── Step 1: Search Jira for similar tickets (async MCP call) ─────────
        similar_tickets = await self._fetch_similar_tickets(query, project)

        # ── Step 2: RAG — past incidents only, using the raw query ─────────
        # Do NOT prefix with generic terms like "incident error bug fix" — that pulls
        # unrelated incident chunks (e.g., CORS incident when user asks about payment).
        chunks, confidence = self.retriever.retrieve(query, project)

        logger.info(
            "TicketAgent: %d similar tickets, %d RAG chunks (confidence=%.3f)",
            len(similar_tickets), len(chunks), confidence,
        )

        # ── Step 3: Build CoT prompt and call LLM ────────────────────────────
        system_prompt    = self.config.get_prompt("system_prompt")
        reasoning_prompt = self.config.get_prompt(
            "ticket_agent_reasoning",
            query=query,
            similar_tickets=_format_similar_tickets(similar_tickets),
            rag_context=_format_rag_context(chunks),
        )

        temperature = self.config.get_temperature("agent_reasoning")   # 0.1
        resp        = await self.llm.generate_structured(reasoning_prompt, system_prompt, temperature, 1000)
        ticket_data = resp.structured if not resp.parse_error else {}

        # ── Step 3b: If assignee is unassigned, fetch real project members and suggest ──
        project_members: list[dict] = []
        if ticket_data.get("title") and ticket_data.get("assignee", "unassigned") == "unassigned":
            project_members = await self._fetch_project_members(project)
            if project_members:
                suggested_member = self._suggest_assignee_member(project_members, similar_tickets)
                if suggested_member:
                    # Store display name for the proposal card, account_id for the Jira API call
                    ticket_data["assignee"]            = suggested_member.get("display_name") or suggested_member.get("name", "unassigned")
                    ticket_data["assignee_account_id"] = suggested_member.get("account_id", "")
                    logger.info("TicketAgent: auto-assigned to '%s' (accountId=%s)", ticket_data["assignee"], ticket_data["assignee_account_id"])

        # ── Elicitation guard (matches the MCP host pattern — Antigravity / Claude
        # Desktop ask for missing required fields before proposing a write). We
        # catch THREE shapes of "the user didn't give enough":
        #   1. extraction failed → no title at all
        #   2. extraction produced a placeholder title (the LLM filled the slot
        #      with the imperative itself when the input was just "create ticket")
        #   3. description is suspiciously short or starts with a meta-phrase
        #      ("the user wants…") — both are LLM hallucination signatures
        _title = (ticket_data.get("title") or "").strip()
        _desc  = (ticket_data.get("description") or "").strip()
        _PLACEHOLDER_TITLES = {
            "ticket", "a ticket", "new ticket", "create ticket", "create a ticket",
            "new jira ticket", "create jira ticket", "open ticket", "open a ticket",
        }
        _META_DESC_PREFIXES = (
            "the user wants", "user wants", "the user is asking",
            "the user requested", "user requested", "the user needs",
        )
        is_placeholder = (
            _title.lower() in _PLACEHOLDER_TITLES
            or _desc.lower().startswith(_META_DESC_PREFIXES)
            or len(_desc) < 30
        )

        if not ticket_data or not _title or is_placeholder:
            reason = (
                "JSON parse failed" if (not ticket_data or not _title)
                else f"placeholder extraction (title={_title!r}, desc={_desc[:40]!r})"
            )
            logger.warning("TicketAgent: %s — asking the user for ticket details", reason)
            clarification = (
                "Please share the ticket details. The standard MCP host pattern "
                "is to collect required fields before creating the issue:\n\n"
                "- **Title** — one-line summary of the issue or request\n"
                "- **Description** — what's the problem / requirement, ideally with "
                "service or endpoint affected and any error message\n"
                "- **Priority** — LOW / MEDIUM / HIGH / CRITICAL (default MEDIUM)\n"
                "- **Assignee** — team member name, or leave blank for unassigned\n\n"
                "**Example:** *Create ticket: Add CSV export to the analytics "
                "dashboard. Users need to export sprint data. Priority HIGH. "
                "Assign to Alice.*"
            )
            return AgentPayload(
                agent_name="ticket_agent",
                confidence=0.0,
                summary="Clarification needed — please provide ticket details",
                structured={"final_response": clarification, "skip_persona": True},
                sources=[],
                hitl_required=False,   # ← do NOT show HITL buttons for a clarification
                hitl_proposal={},
                response=clarification,
            )

        # ── Step 4: Duplicate guard — if LLM found a close match, show it instead of HITL ──
        # When similar_ticket_ref is populated the LLM explicitly identified a closely
        # matching existing ticket. Surface that info (like cross_source would) and skip
        # the creation HITL so the user doesn't accidentally create a duplicate.
        similar_ref = ticket_data.get("similar_ticket_ref", "").strip()
        if similar_ref:
            # Find the full ticket data from the Jira results if available
            matched = next((t for t in similar_tickets if t.get("id", "").upper() == similar_ref.upper()), None)
            if matched:
                resolution = ticket_data.get("similar_ticket_resolution") or matched.get("status", "")
                dup_msg = (
                    f"A similar ticket already exists in Jira:\n\n"
                    f"**{matched['id']}** — {matched['title']}\n"
                    f"**Status:** {matched.get('status', 'UNKNOWN')}\n"
                    f"**Assignee:** {matched.get('assignee', 'unassigned')}\n"
                    f"**Priority:** {matched.get('priority', 'MEDIUM')}\n\n"
                    f"No new ticket was created. If this is a different issue, "
                    f"describe it more specifically and try again."
                )
            else:
                dup_msg = (
                    f"A similar ticket already exists in Jira: **{similar_ref}**.\n\n"
                    f"No new ticket was created. If this is a different issue, "
                    f"describe it more specifically and try again."
                )
            logger.info("TicketAgent: duplicate guard triggered — similar_ref=%s, skipping HITL", similar_ref)
            all_sources = list({c.source for c in chunks})
            if similar_tickets:
                all_sources.append("jira_live")
            return AgentPayload(
                agent_name="ticket_agent",
                confidence=confidence,
                summary=f"Duplicate: {similar_ref}",
                structured={"final_response": dup_msg, "skip_persona": True},
                sources=all_sources,
                hitl_required=False,
                hitl_proposal={},
                response=dup_msg,
            )

        # ── Step 5: Format HITL proposal card ────────────────────────────────
        final_response = _format_ticket_proposal(ticket_data, similar_tickets, project_members or None)

        proposal = {
            "action":              "create_ticket",
            "title":               ticket_data.get("title", ""),
            "description":         ticket_data.get("description", ""),
            "priority":            ticket_data.get("priority", "MEDIUM"),
            "issue_type":          ticket_data.get("issue_type", "Story"),
            "assignee":            ticket_data.get("assignee", "unassigned"),
            "assignee_account_id": ticket_data.get("assignee_account_id", ""),
            "labels":              ticket_data.get("labels", []),
            "project":             project,
            "similar_ref":         ticket_data.get("similar_ticket_ref", ""),
        }

        all_sources = list({c.source for c in chunks})
        if similar_tickets:
            all_sources.append("jira_live")

        return AgentPayload(
            agent_name="ticket_agent",
            confidence=confidence,
            summary=ticket_data.get("title", query[:100]),
            structured={
                "final_response": final_response,
                "ticket_data":    ticket_data,
                "rag_chunks": [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in chunks
                ],
            },
            sources=all_sources,
            hitl_required=True,
            hitl_proposal=proposal,
        )
