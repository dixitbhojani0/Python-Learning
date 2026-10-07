# Daily autonomous engineering run — changelog

## 2026-10-07

**Orientation:** read 2026-10-06's entry + `REDESIGN_BUGS.md`. Open items: E5/E6 (PR lifecycle,
comment edit/delete), E7 (edit/delete ticket + deassign), E10/E11 (regression suite + runner),
E12 remainder (requested), B8 cause 3 (blocked). Well over 5 open — no backlog refill needed.
E5/E6/E7 had all been deferred across several prior runs behind "needs a design decision for how
a user references which entity in natural language" — re-reading the code showed that mechanism
already exists generically (`ticket_agent`'s history-resolution from B10), so the real blocker
was stale, not a missing decision. Picked E7: reading it end-to-end found it two-thirds already
shipped and mis-tracked (edit done, delete deliberately unsupported per `permissions.py`'s own
note) — the one real gap left was deassign, a well-scoped vertical slice across 5 layers.

**Baseline gate (before any change):** backend 137 passed, MCP-server 8 passed, Angular 28 passed
+ `ng build` succeeds — matches 2026-10-06's recorded counts, no regression.

### Items done

1. **E7 — deassign a Jira ticket; corrected E7's stale tracking for edit/delete.**
   `ticket_agent.run()`'s intent regex `\b(assign|reassign)\b` correctly does NOT match the
   embedded "assign" inside "deassign"/"unassign" (no word boundary before it) — so a query
   like "deassign SDLC-5" fell through every intent branch (assign/edit/comment/list) straight
   into the ticket **CREATE** flow, proposing a bogus new ticket instead of clearing the
   assignee. Same bug class B10 already fixed for assign/edit/comment, just never ported to this
   verb. Fixed end-to-end, reusing every existing layer rather than adding new ones:
   - `sdlc-mcp-server/connectors/jira_connector.py`: `deassign_ticket(ticket_id)` — PUTs Jira's
     documented null-`accountId` unassign contract to the same `/assignee` endpoint
     `assign_ticket` already uses.
   - `sdlc-mcp-server/tools/jira_tools.py`: new write tool `jira_deassign_ticket`, same
     `_invalid_ticket_id` boundary guard as the other ticket write tools.
   - `backend/agents/ticket_agent.py`: new `_run_deassignment` (no-op message if already
     unassigned, HITL card otherwise) + a deassign intent branch checked before the assign
     branch, reusing the same history-aware `resolved_ticket_id` (pronoun resolution) the
     assign/edit/comment branches already use.
   - `backend/api/routes/hitl.py` + `backend/auth/permissions.py`: new `deassign_ticket` HITL
     action type + permission tier (developer/manager/technical_leader/admin — not stakeholder,
     matching the capability matrix's existing "assign/reassign/deassign" row). Deliberately a
     **new** action type rather than overloading `assign_ticket` with an empty `account_id` —
     that empty string already means two other things in the existing code (named-target-not-
     found in `_run_assignment`; not-executed in `_execute_assign_ticket`'s truthiness guard), so
     a third meaning would make it ambiguous. No frontend changes — the HITL card renders
     generically from the proposal text, and deassign isn't in E8's `APPROVE_ALL_ELIGIBLE` set
     (a real Jira write, not the low-risk notify tier).
   - **Corrected `REDESIGN_BUGS.md` E7**, which had tracked all of edit/delete/deassign as fully
     open: edit was already shipped in a prior session and never credited (same stale-narrative
     pattern as B1/B2/B4/B5/B7a/B7b/E3/E4/B9); delete is deliberately unsupported, matching
     `permissions.py`'s own 2026-07 note (no MCP tool/connector/agent wiring ever existed for
     it — a pure aspirational matrix entry). This also **answers open question C5** ("real
     delete vs. close/cancel?"): decision is to not support ticket deletion at all — destructive/
     audit-sensitive enough to stay in Jira's own UI, not this assistant's propose→approve model.
   - Design doc: `auto-sdlc/designs/E7.md`.

### Tests added
- `sdlc-mcp-server/tests/test_jira_connector.py` (+2: null-accountId payload, non-204 failure)
- `sdlc-mcp-server/tests/test_tools.py` (extended: `jira_deassign_ticket` in the write inventory/
  classification assertions + an invalid-ticket-id boundary case)
- `ai-sdlc-assistant/tests/unit/test_ticket_agent_deassign.py` (5 cases: proposes HITL,
  "unassign" verb also matches, already-unassigned no-op, no-ticket-id asks rather than falls
  through to CREATE, resolves ticket id from history)
- `ai-sdlc-assistant/tests/unit/test_hitl_deassign_ticket.py` (5 cases: success text, MCP
  failure, MCP exception, stakeholder forbidden, developer allowed)

### Gate status (after all changes)
- Backend: 147 passed (was 137)
- MCP server: 10 passed (was 8)
- Angular: 28 passed (unchanged — no frontend files touched today) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- E5 (PR lifecycle: create PR, edit/delete comments), E6 (edit/delete Jira comment — add-comment
  already shipped) remain open; same shape of fix as E7 (reuse history-resolution, add the one
  missing verb/action), good candidates for a future slice.
- E10/E11 (standing regression suite + automated runner), E12 remainder (GSAP+MotionService
  deliberately deferred — nothing needs it yet; skeleton loaders/admin chart entry are polish,
  not a reproducible gap, so not built speculatively today), B9 lack 2 (A2A, no reproducible
  need), B8 cause 3 (blocked, needs live Confluence access) remain open.

**Needs Dixit:** nothing new blocked this run. B8 cause 3 (live Confluence re-ingest to confirm
the PDF-attachment chunking fix) remains blocked from 2026-10-04/05/06.

## 2026-10-06

**Orientation:** read 2026-10-05's entry + `REDESIGN_BUGS.md`. Well over 5 items open (E5/E6/E7, E8,
B9, E10/E11, E12 remainder, B8 cause 3) — no backlog refill needed. E5/E6/E7 each still need a real
design decision first (how a user references *which* PR comment/ticket to edit/delete in natural
language — flagged again, bigger than today's scope). Picked B9 (quick audit, matches the established
stale-narrative pattern) and E8 (well-specified decision matrix already drafted, requested scope fits
one day).

**Baseline gate (before any change):** backend 130 passed, MCP-server 8 passed, Angular 23 passed +
`ng build` succeeds — matches 2026-10-05's recorded counts, no regression.

### Items done

1. **B9 lack 1 — verified already fixed, not previously credited.** B9 claimed the agent "never lets
   the LLM see a tool result and decide the next tool" — true when B9 was written, stale once B7 Step
   2's `gather_via_tools()` shipped: it already runs a real `bind_tools` ReAct loop (up to 5 rounds,
   each round's tool results appended to the running message list before the model is asked again).
   `MCPAgent` (the live generalist) and `NotifyAgent`'s dynamic-fallback path both route through it.
   Same "stale narrative" pattern already seen for B1/B2/B4/B5/B7a/B7b/E3/E4 — corrected
   `REDESIGN_BUGS.md` rather than re-building what already exists. The 4 fixed-tool specialist agents
   (ticket/risk/pr_review/release_readiness) still call tools directly with no chaining — confirmed
   this is **by design** (already documented under B7 Step 4's "Design distinction" note), not a bug,
   and not converting speculatively with no reproducible symptom. Lack 2 (agent-to-agent/A2A) remains
   open, also with no reproducible need.
   - **Test added:** `tests/unit/test_tool_use_gather_chaining.py` — proves genuine 2-round chaining
     (round 2's tool choice is informed by round 1's actual result content, not just a second
     pre-scripted call), closing the one real gap in the existing gather tests (single-round only).
2. **E8 (requested scope fits today) — HITL Approve / Approve-all (this conversation) / Reject.**
   Low-risk, reversible-ish proposals (`send_slack` — Slack/Teams notify) can now be approved once for
   the rest of a conversation instead of re-prompting on every identical notify. Researched current
   (2026) HITL approval-gate UX guidance first: the standard pattern is exactly the backlog's own
   drafted decision matrix — destructive actions always ask, risky actions get a one-time
   per-session opt-in, safe actions run freely (avoids "approval fatigue").
   - **Scope lives client-side** (`HitlService`, a tab-lived singleton), not a backend Redis session
     flag: a backend flag would force the `check_hitl` orchestrator node to duplicate `hitl.py`'s
     entire action-dispatch chain (role checks, MCP calls, episodic memory, session-store updates) to
     auto-execute without a frontend round-trip — correctness risk for zero gain over just letting the
     frontend fire the existing `/api/hitl/approve` call itself when it decides to auto-approve.
   - `hitl-card` gets a 3rd button only for action types on a small eligible set (`send_slack` today);
     clicking it approves once and remembers the opt-in; every later proposal of that type in the same
     tab's conversation auto-resolves on mount with an "(Auto-approved for this conversation)" note.
     Reload / new session → opt-in gone (session-scoped, not "always allow forever").
   - `HITLRequest.remember` + `ChatResponse.hitl_action_type` (both new) are audit-trail plumbing only
     — `remember` never affects authorization (`require()` still runs every time) or what executes;
     it only adds a "(remembered for this session)" note to the episodic-memory record.
   - Design doc: `auto-sdlc/designs/E8.md`.

### Tests added
- `ai-sdlc-assistant/tests/unit/test_tool_use_gather_chaining.py` (1 test)
- `ai-sdlc-assistant/tests/unit/test_chat_hitl_action_type.py` (3 tests)
- `ai-sdlc-assistant/tests/unit/test_hitl_approval_event_text.py` (3 tests)
- `ai-sdlc-assistant/frontend-angular/src/app/chat/hitl-card/hitl-card.spec.ts` (5 tests — first spec
  for this component)

### Gate status (after all changes)
- Backend: 137 passed (was 130)
- MCP server: 8 passed (unchanged — no MCP-server files touched today)
- Angular: 28 passed (was 23) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- E8's eligible-action-type set has exactly one entry (`send_slack`) because that's the only action
  type that exists today matching the "low-risk, reversible" tier — "Add/edit comment" (medium, from
  the original decision matrix) isn't implemented as a HITL action type at all yet (ties to E6's own
  open "edit/delete comment" gap), so there's nothing to add it to yet. Revisit `agents.yaml`
  per-action risk config (deferred as unneeded ceremony for a 1-entry set) if the eligible set grows.
- B9 lack 2 (agent-to-agent/A2A communication) remains open, no reproducible need.
- E5/E6/E7 (PR lifecycle CRUD, ticket edit/delete/deassign, comment edit/delete), E10/E11 (automated
  eval runner), E12 remainder (GSAP + MotionService, skeleton loaders, admin chart entry,
  `--motion-slow` token) remain open, each larger than fits alongside today's two items. B8 cause 3
  remains blocked (needs live Confluence access, unchanged from 2026-10-04/05).

**Needs Dixit:** nothing new blocked this run — no credential, paid API, or product decision was
needed for today's two items. B8 cause 3 (live Confluence re-ingest) remains blocked from 2026-10-04.

## 2026-10-05

**Orientation:** read 2026-10-04's entry + `REDESIGN_BUGS.md`. Well over 5 items open (B7 Step 4,
E5/E6/E7, E8, B9, E10/E11, E12 remainder, B8 cause 3) — no backlog refill needed. 2026-10-04's
Node-version blocker was already resolved (pinned Node 22.22.3, re-gated green, merged
5f3bfa7/5ff6973) — this session's shell resolved v22.22.3 directly, no Angular gate issue.

**Baseline gate (before any change):** backend 126 passed, MCP-server 8 passed, Angular 23 passed
+ `ng build` succeeds — matches 2026-10-04's recorded counts, no regression.

### Items done

1. **B7 Step 4 — restore the duplicate-ticket HITL suggestion on the live (MCPAgent) path.** The
   last open line under B7: `MCPAgent` (the live generalist, routed to for `cross_source` intent)
   only ever reads — it never offered to file a ticket for a problem described conversationally
   ("the checkout page is throwing 500s for guest users") with no explicit "create a ticket" verb.
   That behavior only ever existed in the dead pre-MCP `cross_source_agent._check_ticket_needed`,
   dropped (not ported) when B7 migrated the live path to MCPAgent. Ported it:
   - New `_check_ticket_needed`/`_format_existing_tickets`/`_format_ticket_suggestion_card` in
     `mcp_agent.py`, wired in after the normal answer is synthesized (never on an early-return
     refusal/outage). Dedup reuses `jira_search_tickets` (the same MCP tool + query-cleaning
     `ticket_agent` already uses for its own similar-ticket check) and the existing
     `cross_source_ticket_suggestion` prompt, unchanged.
   - **No keyword pre-filter** (ties to P1 — "no hardcoded rules"): the old regex gate
     (`_PROBLEM_SIGNALS`/`_RESOLVED_SIGNALS`) is dropped entirely — the prompt's own
     `should_create` rule (real unresolved problem AND not already covered) is already the
     correctness gate, so re-adding a keyword pre-filter would be strictly less correct for no
     benefit. The only skip is reusing `temporal_intent` — already computed in `MCPAgent.run()`
     for the episodic-memory lookup — to skip clearly historical queries for free (zero new code).
   - `hitl_required`/`hitl_proposal` flow through the existing generic HITL plumbing
     (`_payload_to_state` → `check_hitl` node → `execute_create_ticket`) completely unchanged —
     same proposal shape `ticket_agent`/legacy `cross_source_agent` already produce, so the
     approve-execution side needed zero changes.
   - Updated the stale `run_cross_source` docstring (nodes.py) that explicitly named this as a
     pending port, so the next run doesn't re-investigate it; corrected `REDESIGN_BUGS.md`.
   - Design doc: `auto-sdlc/designs/B7-step4.md`.

### Tests added
- `ai-sdlc-assistant/tests/unit/test_mcp_agent_ticket_suggestion.py` (4 cases: proposes for a
  genuine untracked problem, no proposal when an existing ticket already covers it, skips the
  LLM/MCP calls entirely for a historical-intent query, degrades gracefully when the similar-ticket
  search fails)

### Gate status (after all changes)
- Backend: 130 passed (was 126)
- MCP server: 8 passed (unchanged — no MCP-server files touched today)
- Angular: 23 passed (unchanged — no frontend files touched today) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- B7 Step 3's other deferred-from-legacy item still stands: image surfacing from RAG chunks
  (`_images_from_chunks` in the dead `cross_source_agent`) hasn't been ported to MCPAgent either —
  tracked in the `run_cross_source` docstring, not duplicated here.
- E12 remaining (GSAP + `MotionService`, skeleton loaders, admin chart entry, `--motion-slow`
  token) checked again today — still genuinely nothing needs GSAP's timeline/Flip/SplitText
  features yet, and every admin screen already has a spinner-based loading state, so adding
  skeleton loaders now would be unrequested polish, not a gap. Left deliberately deferred, not
  picked up.
- E6 (edit/delete Jira comment), E5 (PR create + edit/delete comments), E7 (edit/delete/deassign
  ticket), E8 (HITL approval scopes), B9 (agentic tool-selection loop), E10/E11 (automated eval
  runner) remain open. E6/E5/E7 each need a real design decision first (how does a user reference
  *which* comment to edit/delete in natural language — Jira comments have no ID surfaced in the
  UI today) — bigger than today's remaining scope, left for a dedicated day with its own design
  doc rather than started and left half-wired.
- B8 cause 3 still needs live Confluence access to confirm the PDF table-detection fix (unchanged
  from 2026-10-04 — no credential available this session either).

**Needs Dixit:** nothing new blocked this run — no credential, paid API, or product decision was
needed for today's item. B8 cause 3 (live Confluence re-ingest) remains blocked from 2026-10-04.

## 2026-10-04

**Orientation:** read 2026-10-02's entry + `REDESIGN_BUGS.md`. Well over 5 items open (B7 Step 4,
B8 cause 3, E5/E7, E8, B9, E10/E11, E12 remainder) — no backlog refill needed. Docker Desktop and
Qdrant were already up this session (unlike 2026-10-02).

**Baseline gate (before any change):** backend 126 passed, MCP-server 8 passed — matches
2026-10-02's recorded counts (122 then +4 from today's new tests... see below), no regression.
Angular could not be baselined in this session — see the Node-version note under item 2.

### Items done

1. **B10 — `pr_review_agent.py`: resolve pronoun PR refs from session history.** Same gap already
   fixed for `ticket_agent.py` on 2026-09-28, confirmed real here too: "approve it" / "assign alice
   as reviewer" right after "review PR-5", with multiple PRs open, hit the ambiguity guard and asked
   "which PR did you mean?" even though the answer was in `state.recent_messages`. Added
   `_last_pr_id_from_history()` (identical newest-turn-first, response-before-query rule) as a
   fallback to `_query_target_pr_id()` — an explicit PR id in the current query still always wins.
   Checked risk/release_readiness/notify agents for the same shape of bug and found none to fix:
   both risk and release_readiness are whole-sprint report generators with no single pronoun target,
   and notify's only entity-like field (channel) has no observed pronoun pattern — not fixing
   speculatively. **Tests:** `tests/unit/test_pr_review_agent_history_resolution.py` (4 new).
2. **E12 (requested) — slice 2: admin sidebar collapse + backlog correction.** Added a toggle button
   in `admin.html`'s `mat-sidenav-content` that flips the existing `mat-sidenav`'s `[opened]` state —
   `mode="side"` + `[opened]` is MatSidenav's own built-in open/close transition, so this needed zero
   new code for the animation itself (no GSAP/MotionService), and the Slice-1 global
   `prefers-reduced-motion` kill-switch already covers it. Also audited E12's other deferred item,
   toast/snackbar: already fully shipped on every admin write action (`MatSnackBar` in rag-manager,
   memory, mcp-servers, sessions, config-viewer) — just never credited in the backlog (same
   stale-narrative pattern as B1/B2/B4/B5/B7a/B7b). Corrected `REDESIGN_BUGS.md`, no code needed there.
   **Tests:** `admin.spec.ts` (new — 2 cases).
   **Environment blocker (not code):** this session's shell resolved Node v20.12.2 on PATH (`nvm4w`),
   below Angular CLI 21's v20.19 minimum, so `ng test`/`ng build` refused to run at all — could not
   execute the Angular gate. Self-reviewed instead: `npx tsc --noEmit` across the project (clean, no
   errors) + manual read-through of `admin.ts`/`admin.html`/`admin.spec.ts` binding names. Prior runs
   through 2026-10-02 recorded `ng test`/`ng build` passing, so `run-daily.ps1`'s own PowerShell
   process likely resolves a different Node on PATH than this interactive git-bash session did — see
   **Needs Dixit** below.
3. **B8 cause 3 — root cause narrowed, not fixed.** Docker/Qdrant were up this session, so queried
   the live `sdlc_knowledge` collection directly for "Clean Code Checklist"'s 5 stored points. Found
   the garbled chunk comes from `ingestion_service.ingest_confluence()`'s **PDF-attachment phase**:
   `unstructured.io` failed to detect this one table as a `Table` element (fell through to
   `NarrativeText`, which *does* get mid-split) — while the same page's **body-text phase** produced
   a correctly formatted markdown table for the same content. The chunker's table-isolation logic
   (`chunk_from_elements`) is correct for every table it successfully detects; this is a detection
   miss on one specific PDF, not a chunking-logic bug. Not fixed today — no live Confluence
   credentials/running backend in this session to pull the actual PDF and confirm a fix without
   guessing blind (would violate "reproduce the failure first"). Documented the precise next step in
   `REDESIGN_BUGS.md` (re-ingest via admin Clear All + Ingest-from-Confluence; if the PDF phase still
   produces a header-less table chunk afterward, that's the reproducible case to fix).

### Tests added
- `ai-sdlc-assistant/tests/unit/test_pr_review_agent_history_resolution.py` (4 tests)
- `ai-sdlc-assistant/frontend-angular/src/app/admin/admin.spec.ts` (2 tests — not run in this
  session, see the Node-version note above)

### Gate status (after all changes)
- Backend: 126 passed (was 122)
- MCP server: 8 passed (unchanged — no MCP-server files touched today)
- Angular: **not run** — `ng test`/`ng build` refused to start (Node v20.12.2 < CLI 21's v20.19
  minimum on this session's PATH). Self-reviewed the one changed component via `tsc --noEmit`
  (clean) + manual review instead. If `run-daily.ps1`'s gate hits the same Node version, it will
  correctly go RED and not merge — a safe failure, not a silent one.

### Left half-done / follow-ups (not blocked, just out of today's scope)
- B8 cause 3: needs live Confluence access to re-ingest and confirm the PDF table-detection fix —
  see item 3 above.
- E12 remaining: GSAP + `MotionService` (still nothing needs timelines/Flip/SplitText/ScrollTrigger),
  skeleton loaders, admin chart entry, `--motion-slow` token.
- B7 Step 4 (MCPAgent write-intent → HITL duplicate-ticket suggestion), E5/E7 (PR lifecycle CRUD,
  ticket edit/delete/deassign), E8 (HITL approval scopes), B9 (agentic tool-selection loop), E10/E11
  (automated eval runner) remain open, each larger than fits alongside today's three items.

**Needs Dixit:**
1. Confirm which Node.js version `run-daily.ps1`'s scheduled-task PowerShell process actually
   resolves on PATH. This interactive session's git-bash shell resolved `nvm4w`'s v20.12.2, which is
   below Angular CLI 21's v20.19 minimum and refuses to run `ng test`/`ng build` at all. If the
   scheduled task hits the same version, every future run's Angular gate goes RED (safe — nothing
   merges — but no Angular work can land until this is fixed). If it's confirmed already using a
   newer Node there, no action needed — just flagging since this session couldn't verify either way.
2. B8 cause 3 needs a live Confluence credential/session to re-ingest "Clean Code Checklist" and
   confirm whether the PDF-attachment table-detection issue reproduces after a fresh ingest.

## 2026-10-02

**Orientation:** read 2026-09-29's entry + `REDESIGN_BUGS.md`. Well over 5 items open (B7c, E3–E8,
B9, E10, E11, E12 remainder, B8 cause 3) — no backlog refill needed. B7c is the only open 🔴 bug
and was user-spotted, so it took priority per the daily-run ordering (failing tests/broken flow >
🔴 bugs > ... > 🟠 enhancements > UI polish).

**Environment note (read before trusting "baseline gate" below):** Docker Desktop was not running
in this session (`docker ps` / `docker compose ps` both failed to reach the daemon — not just the
containers being down, the whole engine). `docker compose up -d qdrant redis` (what `run-daily.ps1`
runs before launching this session) would fail the same way if Docker Desktop isn't set to start
automatically at login/boot on this machine. **Operational risk worth flagging:** pytest exits
non-zero when any test **errors** (fixture/setup failure), not just on an assertion failure — so if
Docker is down when the scheduled task fires, the 7 Qdrant-dependent `test_retriever.py` tests would
ERROR (connection refused), `$be.ok` would be `false`, and the whole gate would go RED and NOT merge
to master, even though every actual test passes. This isn't something to fix by weakening those
tests (they correctly need live Qdrant) — it's a "is Docker Desktop set to auto-start" check worth
Dixit confirming on the machine that runs the scheduled task.

**Baseline gate (before any change):** backend 104 passed + 7 errors (Qdrant connection refused —
environmental, see above; 104 matches 111 total minus the same 7 that need Qdrant), 8 MCP-server,
21 Angular + `ng build` — all non-Qdrant tests green, consistent with no regression since 2026-09-29.

### Items done

1. **B7c — fail loud when a live-data query can't reach MCP.** Previously, ANY MCP outage (server
   down, empty tool list, or every attempted tool call erroring) degraded silently to RAG-only —
   fine for a knowledge question, wrong for "what's the status of SDLC-5?" (could answer from a
   stale doc chunk instead of admitting it can't confirm current state). Added a data-driven signal
   (P1: no keyword list) to `gather_via_tools()`:
   - **MCP server itself unreachable** (`get_mcp_tools()` raises/empty): a one-off LLM
     classification (`needs_live_data_classify` prompt, temperature 0.0) decides if *this query*
     needed live data — the gather loop never got a chance to let the model request a tool itself
     here, so this is the one spot that still asks an LLM directly instead of reading the model's
     own tool-call intent.
   - **Tools available but every attempted call errored:** no extra LLM call — the model already
     decided it needed that tool (it emitted `tool_calls`), so "all attempts failed" IS the
     live-data-needed-but-unavailable signal. Partial success (≥1 call ok) does NOT trip this.
   - New `ToolGatherResult.mcp_unavailable` flag; `MCPAgent.run()` checks it right after the gather
     step (before the domain/low-confidence guards — a stale RAG chunk could otherwise still answer
     a live-data question) and returns an honest "Live tools ... are currently unavailable" message.
   - Pairs with B7a (no silent mock): together, never fabricate, never guess from stale docs when
     the real answer needs a live system.
2. **E3/E4 — ticket status answers always carry priority + latest comment.** The data was already
   fetched (`_normalize_issue` returns priority + comments, per B3), but two gaps meant it didn't
   reliably reach the user: `MCPAgent._enrich_ticket_refs` (the "Live Ticket Statuses" override for
   tickets mentioned in RAG chunks) hand-formatted status/title/assignee/latest-comment but **never
   included priority** — fixed. And `gather_via_tools`'s direct-query path (e.g. "what's the status
   of SDLC-5?") already receives the full ticket JSON via `as_context()`, but `system_prompt` never
   told the model to surface all of it, so an answer could legally stop at "status: IN_PROGRESS" and
   skip the comment that actually carries effort/ETA (the exact "stakeholder sees small fix, dev
   comment says 3-day refactor" gap B3 targets) — added an explicit instruction to always pair
   status+assignee+priority and include the latest comment when one exists. Effort/ETA intentionally
   isn't a dedicated field (E4's design) — the latest comment is the real source of truth.

### Tests added
- `ai-sdlc-assistant/tests/unit/test_tool_use_gather.py` (6 tests — MCP-down+live-data-query,
  MCP-down+knowledge-query, classifier-failure-defaults-safe, no-tool-call-attempted,
  all-attempts-failed, partial-failure-not-flagged)
- `ai-sdlc-assistant/tests/unit/test_mcp_agent_live_data_unavailable.py` (2 tests — short-circuits
  with the honest message, normal path unaffected)
- `ai-sdlc-assistant/tests/unit/test_mcp_agent_ticket_enrichment.py` (3 tests — priority included,
  defaults to MEDIUM when Jira doesn't set one, empty when no ticket ID in chunks)

### Gate status (after all changes)
- Backend: 115 passed (was 104 + 7 Qdrant-environmental errors, both before and after — no
  regression; the 7 errors are Docker-down, not code, see environment note above)
- MCP server: 8 passed (untouched — no MCP-server files changed today)
- Angular: 21 passed (untouched — no frontend files changed today) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- B8 cause 3 (Checklist doc chunking/re-ingest) needs a live Qdrant to verify against — skipped
  today given the Docker-down environment, not re-attempted blind.
- E5/E7 (PR lifecycle CRUD, ticket edit/delete/deassign), E8 (HITL approval scopes), B9 (agentic
  tool-selection loop), E10/E11 (automated eval runner), E12 remainder (GSAP + MotionService, toast,
  sidebar, skeleton loaders) all remain open, each larger than fits alongside today's two items.

**Needs Dixit:** confirm Docker Desktop is set to start automatically on the machine that runs the
`AI-SDLC Daily Loop` scheduled task — see the environment note above. If it isn't, any day Docker
hasn't come up yet when the task fires, the gate goes RED (not merged) purely from the 7
Qdrant-dependent tests erroring, even with zero real regressions. Nothing else blocked this run.

## 2026-09-29

**Orientation:** read 2026-09-28's entry + `REDESIGN_BUGS.md`. Well over 5 items open (E3–E8, B9,
E10, E11, E12, B7 Step 4, B7a, B7b, B7c, B8 cause 3) — no backlog refill needed. E12 (motion
system) is tagged **(requested)**, so it took priority per the daily-run ordering.

**Baseline gate (before any change):** all three green — 111 backend, 8 MCP-server, 18 Angular +
`ng build`.

### Items done

1. **E12 (requested) — motion system, slice 1.** E12 is large ("Big ones can span several days");
   shipped a working, tested first slice rather than the whole item:
   - **Research before building** (per E12's own note + the research-discipline standard): fetched
     `angular.dev/guide/animations` live — `animate.enter`/`animate.leave` are stable since Angular
     **v20.2** (we're on 21.2) and are the team-recommended replacement for the now-deprecated
     `@angular/animations`; confirmed `prefers-reduced-motion` is *not* handled by the framework,
     must be manual CSS. Grepped the whole frontend for legacy `trigger()`/`@angular/animations`
     usage — zero hits, nothing to migrate. Checked GSAP's licence live — 100% free including all
     plugins since Webflow's April 2025 sponsorship, so no blocker for a later slice.
   - **Design doc + motion judgement:** `auto-sdlc/designs/E12.md`, informed by the
     `emil-design-eng` skill (frequency test → bubbles get a short/subtle entrance since they
     appear on every message; HITL card gets a more deliberate one since it's occasional; ease-out,
     never scale-from-zero, transform+opacity only for no layout jank).
   - **Shipped:** `styles.css` motion tokens (`--motion-fast`/`--motion-base`/`--motion-ease-out`)
     + a **global** `prefers-reduced-motion` kill-switch (one rule covers every current/future
     animation, deliberately simpler than a per-component `MotionService` check); `.bubble`
     entrance; a streaming caret shown only while the last assistant message is actively streaming
     (disappears the instant the final response lands); HITL card entrance.
   - **Deliberately deferred, not dropped:** GSAP + `MotionService` (nothing in this slice needs
     Flip/SplitText/ScrollTrigger/timelines — adding the dependency now would sit unused);
     toast/snackbar, sidebar collapse, skeleton loaders, admin chart entry; `--motion-slow` token.
     `REDESIGN_BUGS.md` E12 entry marked ◐ with the exact remaining scope.
2. **B7a/B7b — backlog audit, both already fixed, not previously marked.** Read all 15 `except`
   branches across the 4 live connectors (`sdlc-mcp-server/connectors/*.py`) — zero mock-data
   fallback on error (B7a's exact described bug lived only in the pre-B7-migration legacy backend
   registry, same stale-narrative pattern as B1/B2/B4/B5). Read `server.py` — full OAuth 2.1
   (`AuthSettings`, consent flow, host allowlist) plus `auth/service_token.py`'s constant-time
   `secrets.compare_digest` M2M path already ship (B7b). No code change — corrected the backlog so
   a future run doesn't re-investigate either as open.

### Tests added
- `ai-sdlc-assistant/frontend-angular/src/app/chat/chat.spec.ts` (+3 tests: streaming caret shows
  while loading, hides once loading finishes, hides on an earlier message once a new one streams)

### Gate status (after all changes)
- Backend: 111 passed (untouched — no backend files changed today)
- MCP server: 8 passed (untouched — no MCP-server files changed today)
- Angular: 21 passed (was 18) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- E12 remaining scope: GSAP + `MotionService`, toast/snackbar, sidebar collapse, skeleton loaders,
  admin chart entry, `--motion-slow` token, bundle-growth acceptance check (moot until GSAP lands).
- B7c (fail-loud for live-required ops) is the real, still-open version of what B7a used to
  describe — silent **empty**-on-error (not mock-on-error) can still look like "no results" to a
  caller. Not touched today.
- E3–E8, B9, E10, E11, B7 Step 4, B8 cause 3 remain open, all larger than fit alongside E12 today.

**Needs Dixit:** nothing blocked this run — no credential, paid API, or product decision was
needed.

## 2026-09-28

**Orientation:** read yesterday's entry + `REDESIGN_BUGS.md`. Well over 5 items open (B4, E3–E7,
B8, B9, B10, E8, E10, E11) — no backlog refill needed.

**Baseline gate (before any change):** all three green — 105 backend (matches yesterday's "after"
count), 8 MCP-server, 18 Angular + `ng build`.

### Items done

1. **B10 (partial) — `ticket_agent.py` pronoun/ticket-ID resolution from session history.**
   Tracing the actual `run()` flow surfaced a real bug, not just the documented gap: write-intent
   queries with no explicit ticket ID ("reassign **that ticket** to alice", "log a note on **it**:
   ...") matched `assign_match`/`comment_match` but had no `ticket_id_match`, so the code silently
   fell through every branch into the ticket **CREATE** flow — proposing a bogus new ticket instead
   of acting on the one just discussed. Added `_last_ticket_id_from_history()` (resolves against
   `state.recent_messages`, newest turn first — the same rule `MCPAgent`'s prompt already documents
   for its own pronoun resolution) and a shared `_ask_for_ticket_id()` clarification for when nothing
   resolves. Also discovered `MCPAgent` (the live generalist) already injects `recent_messages` +
   `conversation_summary` + `semantic_context` into its prompt — B10's table was stale, written before
   `MCPAgent` existed; corrected it in `REDESIGN_BUGS.md`. Remaining real gap: the 4 other specialist
   agents (risk/pr_review/release_readiness/notify) still don't consume history — lower priority,
   left open (mostly single-shot queries, not multi-turn pronoun-heavy like ticket ops).
2. **B8 (causes 1+2) — restored full-document recall on the live path.** Reproducing the documented
   symptom (`Clean Code checklist` → wrong, larger doc) showed the real root cause was worse than
   written: `_wants_full_document()`'s keyword-regex recall trigger only ever lived in
   `cross_source_agent.py`, which `run_cross_source` stopped routing to once `MCPAgent` shipped (B2/B5)
   — so full-document recall didn't exist **at all** on the live path, not even the old brittle-keyword
   version. Fixed by extracting the title-overlap logic `identify_document()` already used into
   `HybridRetriever._match_doc_title()` and wiring it into `retrieve_with_corrective_rag()` (what
   `MCPAgent` actually calls): a >=2-word title match on the first-pass chunks triggers
   `retrieve_full_document()` directly — the retrieval signal itself is the recall-intent check, no
   separate keyword list (closes a P1 gap here too). One retrieval call, no perf regression.
   `mcp_agent.py` needed zero changes — it already branches on `rag_strategy=="full_document"` for the
   token budget. Cause 3 (Checklist doc's own chunk quality/fragmentation) is a chunking/ingest problem,
   unrelated to routing — left open.
3. **B4 — verified not reproducible, corrected the backlog.** Its symptom lives entirely in
   `cross_source_agent._check_ticket_needed`, which is dead code on the live path (confirmed zero
   duplicate-ticket logic in `mcp_agent.py`). `TicketAgent`'s own duplicate guard is the only live
   duplicate-detection path today and is already trimmed, not verbose. Ties to B7 Step 4's open
   "give MCPAgent write-intent → HITL proposal" — B4's fix direction applies there when that ships,
   not to the dead code. No code change, backlog corrected so a future run doesn't re-investigate a
   phantom bug (same pattern as yesterday's B1/B2/B5 correction).

### Tests added
- `ai-sdlc-assistant/tests/unit/test_ticket_agent_history_resolution.py` (4 tests)
- `ai-sdlc-assistant/tests/unit/test_retriever.py` (+2 tests: recall-triggers-on-strong-title-match,
  recall-skipped-on-weak-overlap)

### Gate status (after all changes)
- Backend: 111 passed (was 109 after item 1, 105 baseline)
- MCP server: 8 passed (untouched — no MCP-server files changed today)
- Angular: 18 passed (untouched — no frontend files changed today) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- B10: risk/pr_review/release_readiness/notify agents still don't consume `recent_messages` or
  `semantic_context`. `ContextBuilder` (7-slot tiktoken budget) remains a dead module — MCPAgent builds
  its prompt inline instead; harmless but worth deleting or actually adopting later, not both.
- B8 cause 3 (Checklist doc chunking/fragmentation) still open — needs a rechunk + re-ingest pass.
- B9 (dynamic tool-selection loop for specialists), E8 (HITL approve-all scoping), E10/E11 (automated
  eval runner) remain open, all larger than a one-day slice.

**Needs Dixit:** nothing blocked this run — no credential, paid API, or product decision was needed.

**Orientation:** no prior CHANGELOG entry existed (first real run — earlier commits on this
branch were harness/infra work, not backlog items). Read `ai-sdlc-assistant/REDESIGN_BUGS.md`;
well over 5 items were open, so no backlog refill was needed.

**Baseline gate (before any change):** all three green — 95 backend, 6 MCP-server, 13 Angular.

### Items done

1. **B1/B2/B5 — backlog audit + regression test.** These were marked open (B1 "◐ PARTIAL", B2/B5
   "☐"), but reading the actual code showed all three already fixed by the earlier B7 MCP
   migration:
   - B1 cause 2 (a done ticket still showing as a blocker): `sdlc-mcp-server/connectors/jira_connector.py`
     `get_blocked_tickets()` already sends `AND statusCategory != "Done"` in its JQL. Added
     `sdlc-mcp-server/tests/test_jira_connector.py` to lock this in — no test existed for it before,
     so a future edit could silently reintroduce the bug with nothing to catch it.
   - B2/B5 (two ticket-creation paths / inconsistent routing): `backend/orchestrator/actions.py`
     `execute_create_ticket()` is the one execution path today, and `run_cross_source` routes to
     `MCPAgent`, not the legacy `CrossSourceAgent` that used to produce the phantom `SDLC-1043`.
   - Updated `REDESIGN_BUGS.md` to reflect verified reality instead of stale investigation notes.

2. **E9 — "Why this answer?" decision-trace panel.** Backend already computed the pieces
   (`llm_classify`'s routing reason, `MCPAgent`'s `mcp_calls`/`rag_chunks`) and dropped them before
   the response ever reached the UI. Wired them through end-to-end:
   - `classifier.llm_classify()` now returns `(agents, reason)`; `SDLCState.routing_reason` added;
     `chat.py::_build_trace()` assembles `ChatResponse.trace` (omitted — not empty — when there's
     nothing to explain: HITL proposal, blocked query, no-evidence refusal).
   - Angular: native `<details>`/`<summary>` panel under the existing meta-chip row in the chat
     component — no new dependency, reuses the established `.source-chip`/`.confidence` palette.
   - Design doc + static mockup: `auto-sdlc/designs/E9.md` / `E9.html`. Google Stitch and Claude
     Design weren't available in this environment, so the mockup was hand-built against the
     existing `chat.css` palette instead of generated.

### Tests added
- `sdlc-mcp-server/tests/test_jira_connector.py` (2 tests)
- `ai-sdlc-assistant/tests/unit/test_classifier_routing_reason.py` (3 tests)
- `ai-sdlc-assistant/tests/unit/test_chat_trace.py` (7 tests)
- `ai-sdlc-assistant/frontend-angular/src/app/chat/chat.spec.ts` (5 tests — first spec for this component)

### Gate status (after all changes)
- Backend: 105 passed (was 95)
- MCP server: 8 passed (was 6)
- Angular: 18 passed (was 13) + `ng build` succeeds

### Left half-done / follow-ups (not blocked, just out of today's scope)
- B7 Step 4's own open item still stands: MCPAgent doesn't yet raise a duplicate-ticket
  *suggestion* (the old cross_source behavior) — tracked there, not duplicated here.
- E9's trace is populated by `MCPAgent` only; the five specialist agents (ticket/risk/pr_review/
  release_readiness/notify) don't set `mcp_calls`/`rag_chunks` in their payload, so their
  responses will show routing-reason-only (or no panel) — this is correct today (fixed-tool-need
  agents, not LLM-picked), not a bug, but worth a follow-up if those agents grow tool-selection logic.
- B8 (RAG chunking/recall) and B10 (memory injection for the 5 specialist agents) remain open,
  both larger than a one-day slice.

**Needs Dixit:** nothing blocked this run — no credential, paid API, or product decision was needed.
