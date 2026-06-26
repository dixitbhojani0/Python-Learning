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
        """Search Jira for tickets similar to the reported issue."""
        if self.mcp is None:
            return []
        try:
            tickets = await self.mcp.get("jira").search_tickets(query, project)
            logger.info("TicketAgent: found %d similar Jira tickets", len(tickets))
            return tickets
        except Exception:
            logger.exception("TicketAgent: Jira search failed — proceeding without similar tickets")
            return []

    async def _fetch_project_members(self, project: str) -> list[dict]:
        """Fetch all assignable members for the project from Jira."""
        if self.mcp is None:
            return []
        try:
            return await self.mcp.get("jira").get_project_members(project)
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

        ticket, members = {}, []
        if self.mcp:
            results = await asyncio.gather(
                self.mcp.get("jira").get_ticket(ticket_id),
                self.mcp.get("jira").get_project_members(project),
                return_exceptions=True,
            )
            ticket  = results[0] if not isinstance(results[0], Exception) else {}
            members = results[1] if not isinstance(results[1], Exception) else []

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

        if not ticket_data or not ticket_data.get("title"):
            logger.warning("TicketAgent: JSON parse failed — returning clarification instead of fallback proposal")
            # Don't create a garbage ticket. Tell the user what they need to provide.
            clarification = (
                "I wasn't able to extract enough detail to propose a ticket from your message.\n\n"
                "To create a Jira ticket, please describe the issue more specifically, for example:\n"
                "- What is the error message or symptom?\n"
                "- Which endpoint or service is affected?\n"
                "- When did it start happening?\n\n"
                "If you were asking about existing blocked tickets, try: "
                "**\"Which tickets are blocked in sprint 12?\"**"
            )
            return AgentPayload(
                agent_name="ticket_agent",
                confidence=0.0,
                summary="Clarification needed",
                structured={"final_response": clarification},
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
