# B7 Step 4 — MCPAgent write-intent → HITL ticket proposal

## Problem
`MCPAgent` (the live generalist, routed to for `cross_source` intent) only ever
*reads*: `gather_via_tools()` is restricted to read-only MCP tools by design
(`client.is_write_tool()` keeps the LLM from ever firing a write on its own).
So when a user describes a problem conversationally — "the checkout page is
throwing 500s for guest users" — with no explicit "create a ticket" verb, the
classifier routes it to `cross_source` (not `ticket`), MCPAgent answers the
question, and the untracked issue is never surfaced for tracking. The old
(dead-code, pre-MCP-migration) `cross_source_agent.py` had this via
`_check_ticket_needed` + a regex pre-filter (`_PROBLEM_SIGNALS`/
`_RESOLVED_SIGNALS`); B7's migration to `MCPAgent` dropped it rather than
porting it. Tracked as B7 Step 4's last open line.

## SDLC phase
Build/operate — surfaces an untracked defect at the point it's reported in
conversation, same as triage would, instead of requiring a human to notice
and separately file it.

## User flow
1. User describes a problem to the chat (no explicit ticket-creation phrasing).
2. Classifier → `cross_source` → `MCPAgent.run()`.
3. MCPAgent answers normally (RAG + live MCP data, unchanged).
4. **New:** once a real answer exists (not an early-return refusal/outage) and
   the query isn't purely historical, ask the LLM — using the investigation
   answer + a live Jira similar-ticket search — whether this is a genuine,
   untracked problem (`cross_source_ticket_suggestion` prompt, already in
   `prompts.yaml`, reused as-is).
5. If yes: the response becomes the answer + a "🎫 Untracked Issue Detected"
   card (reusing `_format_ticket_suggestion_card`'s layout), and the payload
   sets `hitl_required=True` / `hitl_proposal={"action": "create_ticket", ...}`
   — the existing generic HITL card (Angular) and `execute_create_ticket`
   (approve-side, unchanged) take it from there, identically to how
   `ticket_agent` proposals already work today.
6. If no (already tracked, or not actually a problem): response is unchanged.

## API / MCP / schema changes
None. Reuses:
- MCP read tool `jira_search_tickets` (same tool + query-cleaning
  `ticket_agent._clean_ticket_query` already uses for its own dedup).
- Prompt key `cross_source_ticket_suggestion` (unchanged, already defined).
- `AgentPayload.hitl_required` / `hitl_proposal` (existing fields on every
  agent payload) → `_payload_to_state` (nodes.py, unchanged) → existing
  `check_hitl` node → existing `execute_create_ticket` action.

## Why no keyword pre-filter (ties to P1 — no hardcoded rules)
The backlog's own P1 table flags `_PROBLEM_SIGNALS`/`_RESOLVED_SIGNALS` regex
as a spot to convert to probabilistic ("let the LLM decide if an untracked
issue exists"). The `cross_source_ticket_suggestion` prompt already *is* that
correctness gate (rules: only `should_create=true` for a real unresolved
problem not already covered by an existing ticket) — the regex was purely a
cost pre-filter, not a correctness requirement. Dropping it entirely is both
more correct per the project's own stated design principle and less code.
The one gate kept is reusing `temporal_intent` (already computed in
`MCPAgent.run()` for the episodic-memory lookup) to skip clearly
**historical** queries ("how was X fixed") — free (no new computation), and
narrows exactly the case the old `_RESOLVED_SIGNALS` regex excluded, without
adding a new regex of its own.

## UI states
None new — the existing generic HITL card (`hitl-card.html`/`.ts`) renders any
`action`/proposal shape via Approve/Reject; it is not keyed per agent. No
Angular changes.

## Acceptance criteria
- A `cross_source`-routed query describing an unresolved problem, with no
  existing Jira ticket covering it, returns `hitl_required=True` and a
  `create_ticket` proposal with title/description/priority/labels filled in.
- The same query, when an existing ticket already covers the issue (per the
  LLM's own comparison against the live similar-ticket search), does **not**
  set `hitl_required` — response is the plain answer.
- A purely historical query ("how was the SDLC-5 outage resolved?") never
  triggers the extra LLM/MCP call at all (skipped via `temporal_intent`).
- Early-return paths (domain guard, low-confidence guard, mcp_unavailable)
  are unaffected — the check only runs after a real answer is produced.
- Approving the resulting card creates the ticket via the existing
  `execute_create_ticket` path — zero changes needed there (same proposal
  shape `ticket_agent`/legacy `cross_source_agent` already produce).

## Test plan
`tests/unit/test_mcp_agent_ticket_suggestion.py`:
1. Real untracked problem + no similar tickets → `should_create=true` →
   payload has `hitl_required=True`, proposal has the right `action`/title.
2. Similar ticket already exists → LLM returns `should_create=false` →
   `hitl_required` stays `False`, response unchanged.
3. Historical-intent query → the ticket-suggestion LLM call and the Jira
   search are never invoked (asserted via mock `assert_not_called`).
4. MCP similar-ticket search raises → degrades to an empty existing-tickets
   list (doesn't crash the whole turn) — mirrors `ticket_agent`'s own
   fail-soft pattern for the same call.
