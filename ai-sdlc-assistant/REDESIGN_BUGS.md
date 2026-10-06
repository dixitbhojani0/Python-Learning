# AI SDLC Assistant — Bug & Redesign Tracker

> Living document. Goal: make the assistant genuinely useful to **developer / manager /
> stakeholder** with clear communication and correct role-based actions. Add new
> bugs/enhancements as they're found. **Nothing here is fixed yet — this is the checklist.**

Severity: 🔴 demo-breaking · 🟠 important · 🟡 polish
Status: ☐ open · ◐ investigating · ☑ fixed

---

## A. Confirmed bugs

### 🔴 B1 — Stale risk/blocker after Jira update (cache + blocked-detection)  ☑ FIXED (verified 2026-09-27)
- **Cause 1 (cache) — FIXED via B6:** the response cache is gone entirely, so no query can serve a
  stale risk report anymore (moot — there's no cache left to bypass).
- **Cause 2 (blocked-detection) — VERIFIED FIXED:** `sdlc-mcp-server/connectors/jira_connector.py`
  `get_blocked_tickets()` JQL already carries `AND statusCategory != "Done"` (line ~213), so Jira
  itself excludes done tickets server-side — a done ticket can no longer come back as a blocker.
  Belt-and-suspenders: `MCPAgent._enrich_ticket_refs` (`backend/agents/mcp_agent.py`) additionally
  fetches live status for every ticket ID mentioned in RAG chunks and the prompt's data-precedence
  directive states "A ticket listed as DONE ... is NOT blocked — ignore any contradicting status
  claim in the document content below."
- **Regression test added:** `sdlc-mcp-server/tests/test_jira_connector.py` —
  `test_get_blocked_tickets_jql_excludes_done_status` asserts the JQL clause is present, so a future
  edit that drops the filter fails the gate instead of silently reintroducing B1.
- **Original symptom (now historical):** Updated SDLC-1 to *done* in Jira; "are we at risk of missing
  the sprint?" still listed SDLC-1 as a blocker.
- **Where:** `sdlc-mcp-server/connectors/jira_connector.py` `get_blocked_tickets`; `backend/agents/mcp_agent.py`.

### 🔴 B2 — Two ticket-creation paths produce different / wrong ticket numbers  ☑ FIXED (verified 2026-09-27)
- **Fixed by the B7 MCP migration:** `backend/orchestrator/actions.py` `execute_create_ticket()` is now
  the **one** execution path for HITL-approved ticket creation (shared by the REST approve route and the
  Slack Approve button), and it always calls `jira_create_ticket` over real MCP — no local fallback
  counter, no fabricated `SDLC-1043`. On failure it returns an honest error instead of inventing an id.
- **Also confirmed:** `run_cross_source` (`nodes.py:361`) now routes to `MCPAgent`, not the legacy
  `CrossSourceAgent` — so the old `_check_ticket_needed` / `_format_ticket_suggestion_card` path that
  produced the phantom number is **dead code** on the live path (kept only for one-line revert).
- **Gap this surfaces (tracked separately, not a bug):** MCPAgent doesn't yet raise a duplicate-ticket
  *suggestion* itself — see B7 Step 4 "Still TODO: give MCPAgent write-intent → HITL proposal".
- **Original symptom (now historical):** cross_source's suggestion path produced **SDLC-1043**
  (mock blocked-list max **+1**) while ticket_agent produced the correct **SDLC-11**.
- **Where:** `backend/orchestrator/actions.py` `execute_create_ticket`; `backend/orchestrator/nodes.py`.

### 🔴 B3 — Ticket comments not surfaced (the core communication gap)  ☑ FIXED (real path)
- **Fixed:** `jira_connector._normalize_issue` now extracts the latest ≤5 `comment`s (author/date/body via
  ADF) into a `comments` field. `get_ticket` fetches the `comment` field, so ticket-detail answers now carry
  the developer's real comments — the gather→synthesize agent surfaces them (no per-formatter change). Real
  Jira is active (JIRA_TOKEN set); mock not polished (not in path). Effort/ETA usually lives in comments.
- **Symptom / intent:** A stakeholder sees "small syntax fix"; the developer's comment says it's
  actually a 3-day refactor. That detail must reach whoever asks for status.
- **State:** real connector *fetches* comments (`jira_connector.py:133` includes `comment` field),
  but the response formatters likely show only title/status/assignee — **comments aren't rendered**.
- **Where:** ticket-detail formatting in `cross_source_agent.py` / `ticket_agent.py`.
- **Fix direction:** include the latest/most-relevant developer comment(s) + any effort/ETA in the
  ticket-detail answer, for all roles (phrased per persona).

### 🟠 B4 — Over-eager ticket matching + verbose suggestion  ☐ NOT REPRODUCIBLE ON LIVE PATH (checked 2026-09-28)
- **Symptom (original):** "monitoring detected 12% 500 errors" → matched **SDLC-6** by similarity; when
  told "this is different", it asked for a new ticket but **dumped excessive details**.
- **Where the bug lives:** `cross_source_agent.py` `_check_ticket_needed` — but `run_cross_source` routes
  to `MCPAgent` today, not `cross_source_agent` (B2/B5), and `MCPAgent` has **no** duplicate-ticket
  suggestion logic at all yet (confirmed: zero hits for `duplicate|similar_ticket|_check_ticket_needed` in
  `mcp_agent.py`). So this exact symptom can't reproduce live — `TicketAgent`'s own duplicate guard
  (`similar_ticket_ref`, step 4 in `ticket_agent.py::run`) is the only live duplicate-detection path today,
  and it already trims the reply to id/title/status/assignee/priority, not a dump.
- **Ties to B7 Step 4's open TODO:** "give MCPAgent write-intent → HITL proposal" — if that ships, apply
  this fix (stricter match threshold + trimmed card) there, not in the dead `cross_source_agent` code.
- **Fix direction (when the above ships):** stricter duplicate match (don't claim a ticket on weak
  similarity); trim the proposal to title / short description / priority / labels only.

### 🟠 B5 — Routing inconsistency for ticket creation  ☑ FIXED (verified 2026-09-27, ties to B2)
- **Confirmed:** `run_cross_source` routes to `MCPAgent` (read-only generalist, no ticket-creation
  proposal today — see B7 Step 4 TODO), and `TicketAgent` (`nodes.py` `run_ticket`) is the only routed
  node that produces a `create_ticket` HITL proposal. There is exactly one live path today; the old
  "cross_source sometimes handles create-intent" behavior no longer exists (legacy agent unrouted).

### 🔴 B6 — Redis semantic cache is wrong for a live-data system  ☑ FIXED
- **Fixed (Option A — cache off):** response cache fully removed — read node (`nodes.py check_semantic_cache`
  now a no-op passthrough), write (`chat.py set_cached`), invalidate (`hitl.py` approve+reject), and the
  hardcoded `_LIVE_QUERY_PATTERNS` / `_is_live_query` gate (P1 win) all deleted. `SemanticCache` class kept
  but unimported, revivable as a data-driven RAG-only cache if ever justified. Semantic/session/episodic
  memory untouched. Every query now answers fresh.
- **Problem:** This assistant is **mostly live data** (Jira/GitHub/Slack via MCP, changing constantly).
  Caching whole answers serves **stale data** — this is the direct cause of **B1** (updated Jira,
  got the old answer back). A cache keyed only on query text cannot know the underlying MCP state changed.
- **Decision (user):** **stop / comment down** the response cache. If we keep any caching at all, it
  must be **RAG-only** (static document content that doesn't change between ingests) — **never** for
  answers that used MCP/live data.
- **Current state — note the anti-pattern:** cacheability is gated by a **hardcoded keyword list**
  (`_LIVE_QUERY_PATTERNS`, `nodes.py:118`). That list is brittle (it already missed "are we at risk" →
  B1). This is exactly the hardcoding we want to remove (see Principle P1).
- **Where:** `memory/redis_cache.py` (`semantic_cache`); `chat.py` `set_cached`; `nodes.py`
  `check_semantic_cache` / `route_cache` / `_is_live_query`.
- **Fix direction (data-driven, not keyword-driven):** decide cacheability from **what sources the
  answer actually used** — if any agent payload pulled MCP/live data → **do not cache**; cache only
  pure-RAG answers (and even then short TTL). Simplest interim: disable the response cache entirely.

---

## B. Enhancements / redesign — role-based assistant

The assistant should behave per role. Target capability matrix:

| Capability | Developer | Manager | Stakeholder |
|---|---|---|---|
| View ticket details **incl. comments + effort/ETA** | ✅ | ✅ | ✅ |
| Create ticket | ✅ | ✅ | ✅ (→ notifies developer) |
| Edit / delete ticket | ✅ | ✅ | ✕ |
| Add / edit comment | ✅ | ✅ | (✅ note only?) — TBD |
| Assign / reassign / **deassign** | ✅ | ✅ | ✕ |
| PR: create / approve / reject | ✅ | ✅ (approve) | ✕ |
| PR: add / edit / delete comment | ✅ | ✅ | ✕ |
| Notifications (Slack/Teams) | receive | send/receive | send on create |

- **E1 — Capability/permission model:** ☑ **DONE** — `backend/auth/permissions.py` (RBAC matrix +
  `can`/`require`) enforced at the HITL execution layer (`hitl.py` `require(user.role, action_type)`).
  developer = team writes (no release sign-off); manager/technical_leader/admin = all; stakeholder =
  create_ticket + send_slack only. Verified by self-check. (Release manager-gate folded into the matrix.)
- **E2 — Stakeholder creates ticket → Slack notification to developer** ☑ **DONE** — `_execute_create_ticket`
  fires `slack_send_message` (over MCP) to `#backend` when `approver_role == "stakeholder"`, so a dev
  triages it and adds real effort/comments. Reuses the existing write tool; non-fatal if Slack fails.
- **E3 — Status queries return full detail: status, assignee, priority, latest comment, effort/ETA**
  ☑ **DONE (2026-10-02):** the data was always fetched (`_normalize_issue` returns priority +
  comments already, per B3), but one formatter dropped it and nothing told the LLM to always
  surface it. Two fixes: (1) `MCPAgent._enrich_ticket_refs` — the "Live Ticket Statuses" section
  for tickets mentioned in RAG chunks — hand-formatted a line with status/title/assignee/latest
  comment but **never included priority**; added it. (2) `gather_via_tools`'s direct-query path
  (e.g. "what's the status of SDLC-5?") already receives the full normalized ticket JSON
  (priority + comments included) via `as_context()`, but `system_prompt` never told the model to
  surface all of it — added an explicit rule: "always surface status, assignee, AND priority
  together, plus the latest comment if one exists ... do not answer with status alone." Effort/ETA
  intentionally isn't a dedicated field (E4) — the latest comment carries it, same as B3.
  **Where:** `backend/agents/mcp_agent.py` (`_enrich_ticket_refs`), `config/prompts.yaml`
  (`system_prompt`). **Tests:** `tests/unit/test_mcp_agent_ticket_enrichment.py` (3 cases).
- **E4 — Ticket comments = source of truth** for "how big / how long", so stakeholders get real
  expectations instead of assumptions (directly supports B3) ☑ **DONE** — covered by E3's fix
  above (the latest comment is now guaranteed to surface alongside status/assignee/priority,
  both in the RAG-referenced-ticket path and the direct-query path); no separate effort/ETA field
  needed, by design (comments are the real source, not a Jira custom field no one fills in).
- **E5 — Full PR lifecycle:** create / approve / reject / comment CRUD. We have assign-reviewer +
  approve (mock-safe); reject = HITL cancel. Missing: create PR, edit/delete comments. ☐
- **E6 — Comment on Jira:** ◐ **add-comment DONE** — full chain: connector `add_comment` (+ mock) →
  MCP write tool `jira_add_comment` → HITL `comment_ticket` branch (approve/reject) → `ticket_agent`
  `_run_comment` intent detection ("add a comment to SDLC-5: …", excludes reads) + body extraction →
  routing keywords + `comment_ticket` permission. This is the WRITE side of B3 (dev logs effort). 
  **edit/delete comment** still TODO.
- **E7 — Edit/delete ticket + deassign:** connector methods + HITL actions + permission gate. ☐

---

## C. Open questions (to decide before the fix pass)

1. **Canonical ticket-creation path** — consolidate on ticket_agent? (recommended) ☐
2. **Real Jira confirmed active** (SDLC-11 created live) — so why did the cross_source path yield a
   mock-style SDLC-1043? (B2 investigation) ☐
3. **Permission enforcement point** — gate in HITL approve (role check) vs. at agent routing? ☐
4. **Stakeholder comment rights** — can they comment, or only create + view? ☐
5. **Delete semantics** — real delete vs. close/cancel? (real Jira delete is destructive) ☐

---

## E. Design principles (must hold across the redesign)

### P1 — No hardcoded rules; decisions are probabilistic  🔴
Every decision — **which agent**, **whether to cache**, **what action to take**, **which retrieval
strategy**, **is this a duplicate ticket**, **is this in-domain** — must be derived from the **query +
the data we have + model confidence** (LLM supervisor, embeddings, reranker scores), **not** from
hardcoded keyword/regex lists. Hardcoding is brittle (it caused B1 and B6) and defeats the point of an
agentic system. Deterministic code is allowed **only** for:
  - **safety fallbacks** when the LLM is unavailable (rate limit / parse error), and
  - **legitimate IR signals** (BM25 / title lexical match — these are *retrieval features*, not rules).

**Current hardcoded spots to convert to probabilistic (or justify as fallback-only):**
| Spot | File | Action |
|---|---|---|
| ~~`_LIVE_QUERY_PATTERNS` (cache bypass by keywords)~~ | ~~`nodes.py:118`~~ | ☑ **removed** — cache off entirely (B6) |
| `keyword_classify` trigger lists | `classifier.py` + `agents.yaml` | keep **only** as LLM-down fallback; LLM is primary ✅ already |
| `_wants_full_document` regex (recall intent) | `cross_source_agent.py` | let retrieval/LLM decide recall vs top-k |
| `_classify_temporal_intent` regex (current/historical) | `cross_source_agent.py` | infer from data freshness + LLM, not keywords |
| `_PROBLEM_SIGNALS` / `_RESOLVED_SIGNALS` regex (ticket suggestion) | `cross_source_agent.py` | let the LLM decide if an untracked issue exists |
| `_RECALL_STOPWORDS` title match | `retriever.py` | OK as lexical IR signal (keep) — borderline |

> Reconciliation note: routing is already **LLM-first** (good). The remaining regex gates above are the
> next targets. Aim: deterministic code only as fallback/IR-feature, never as the primary decision.

---

## D. Newly reported (append below as you find more)
- **B6** (Redis cache) and **P1** (no-hardcode principle) added from your latest note — see above.

### 🔴 B7 — We did NOT build real MCP; connectors are direct REST clients  ◐ IN PROGRESS
- **Decision:** Option B — build our **own FastMCP server** (wrapping existing connectors) + a host-side
  **MCP client** (`langchain-mcp-adapters`), LLM-driven tool-use. Real MCP end-to-end, free/local,
  reuses connectors + mocks, offline demo. Streamable-HTTP transport = decoupled, scalable service.
- **Build sequence (each step keeps the demo green):** 0 spike ▸ 1 server(read tools) ▸ 2 client+tool-use
  node ▸ 3 migrate cross_source ▸ 4 write tools behind HITL ▸ 5 finish migration ▸ 6 (stretch) official server.
- **Step 0 — ☑ DONE + VERIFIED:** deps added (`mcp` 1.28, `langchain-mcp-adapters` 0.1.14 — pinned to
  the LangChain-0.3-compatible line). `backend/mcp_server/server.py` (FastMCP, streamable-HTTP),
  `backend/mcp_client/client.py` (`get_mcp_tools()`), `scripts/mcp_spike.py`. **Live run passed:** protocol
  2025-11-25 negotiated, `tools/list → ['ping']`, LLM selected+called `ping` over JSON-RPC, `pong` returned.
- **Provider-agnostic seam added:** `BaseLLMProvider.get_chat_model()` (impl in GroqProvider) → tool-use
  loop never references a concrete provider. Swap provider = no MCP/agent change. (Addresses scalability ask.)
- **Step 1 — ☑ DONE + VERIFIED (read tools):** `backend/mcp_server/tools/` now has **6 modules**
  (jira·github·slack·confluence·teams·drive), each `register(mcp, registry)` delegating to the existing
  connectors — **16 MCP tools** total. Live runs passed: LLM chose `jira_get_ticket` for "status of SDLC-5"
  and `github_list_open_prs` (hit **real GitHub**) for "open PRs", entirely from tool descriptions.
- **Tool-design rule (learned live):** Groq strictly validates tool calls against the advertised JSON
  schema and the model mistypes numbers (sent `"limit":"10"` for an int → 400). So MCP tools use
  **string params only; avoid int/scalar knobs**; keep limits/pagination internal. Applies to Step-4 writes too.
- **Tool-use robustness rule (learned live):** a bare `create_react_agent` loops and leaks Llama's
  `<|python_tag|>` tool syntax into the answer. Fix = **guiding system prompt + recursion cap**; with both,
  answers come back as clean prose. Step 2's real node carries this. (Confluence MCP returned empty — verify
  the `SDLC` space has pages / B7a; its content is also in RAG so low-impact.)
- **Step 2 — ☑ DONE + VERIFIED (gather loop):** `backend/mcp_client/tool_use.py` `gather_via_tools()` —
  provider-seam chat model + `bind_tools` manual loop (recursion-capped), returns live data + call trace,
  **no answer** (gather-then-synthesize, user's choice). Live runs: chained jira+github for "blockers+PRs",
  jira+slack for "SDLC-5 + Slack". `scripts/mcp_gather_probe.py` verifies.
  - minor: `github_list_open_prs` returns list-of-JSON-strings (normalize in synthesis); SDLC-1 returned
    `status=DONE` by `get_blocked_tickets` → **confirms B1 cause-2** (stale `blocked` label in JQL).
- **Step 3 — ☑ BUILT (pending live verify):** `backend/agents/mcp_agent.py` `MCPAgent` — RAG (corrective)
  + `gather_via_tools` (LLM picks/chains real MCP tools) → synthesized via prompts.yaml/provider →
  same AgentPayload (persona/faithfulness/chips/HITL intact). Live MCP data placed before RAG (fresh wins).
  `nodes.run_cross_source` now routes to MCPAgent (legacy `CrossSourceAgent` kept for 1-line revert).
  Verify: `scripts/mcp_agent_probe.py` (needs MCP server + Qdrant up).
  - **Operational:** the FastAPI host now needs the **MCP server running** (own process/Docker service);
    if down, gather returns empty → graceful RAG-only.
  - **Deferred from legacy cross_source (port deliberately):** image surfacing (later), duplicate-ticket
    suggestion → returns via **Step 4** write tools + HITL; reranker MCP relevance-gate (later).
- **Step 4 — ◐ infra BUILT (HITL rewiring pending live test):**
  - **Write tools on the server** (`register_writes` in jira/github/slack tools): `jira_create_ticket`,
    `jira_assign_ticket`, `jira_update_ticket`, `github_assign_reviewer`, `github_approve_pr`,
    `slack_send_message` → delegate to existing connector write methods. Server now exposes 16 read + 6 write.
  - **Read-only autonomous loop (safety):** `client.is_write_tool()` (verb-based) — `get_mcp_tools()`
    returns READ-ONLY by default, so the LLM can NEVER fire a write. Verified: 6 writes excluded, 16 reads kept.
  - **`call_mcp_tool(name, args)`** — execution-path helper to invoke a write over MCP (for 4d).
  - **4d — ☑ DONE (live approve/reject test pending Docker):** every `hitl.py` approve-execution write now
    runs over MCP via `_mcp_write` → `call_mcp_tool` (create_ticket, assign_ticket, assign_reviewer,
    approve_pr, release-notify, send_slack). Role gates / NO_GO block / result text unchanged. `_mcp_write`
    normalizes MCP string/list results to dicts. So the WRITE path is now real-MCP, same as reads.
    - **Bonus B2 fix:** dropped the `_fallback_counter` — ticket creation is now ONE path (MCP
      `jira_create_ticket`, real-or-mock on the server). No more fabricated `SDLC-1043`; honest error if create fails.
    - **Bonus bug fixed:** `ctx` was left undefined by the B6 cache removal (would `NameError` on every
      approval at the episodic record) — restored `ctx = action.get("context", {})`.
  - **All 5 specialist agents migrated to MCP ☑:** risk · ticket · pr_review · release_readiness · notify
    now call their tools via `call_mcp_tool(...)` (real MCP server), not the in-process registry. Structured
    logic unchanged — `call_mcp_tool` returns NORMALIZED dict/list (centralized `normalize_tool_result`).
    Added read tool `jira_get_project_members`. Legacy `cross_source_agent` keeps `self.mcp.get()` but is
    not routed to (MCPAgent is the live generalist).
    - **Design distinction (standard):** generalist (MCPAgent) = LLM picks tools (`gather_via_tools`);
      specialists = fixed tool needs → call specific tools via `call_mcp_tool`. Both go through real MCP.
  - **Duplicate-ticket suggestion ☑ DONE (2026-10-05):** `MCPAgent` now offers to file a ticket when
    an answer surfaces a genuine, untracked problem — ported from the dead `cross_source_agent`
    (`_check_ticket_needed`/`cross_source_ticket_suggestion` prompt, reused as-is) onto real MCP
    (`jira_search_tickets` for dedup, same tool `ticket_agent` already uses). No keyword pre-filter
    (P1): the LLM's own `should_create` rule is the gate; only reuses the already-computed
    `temporal_intent` to skip historical queries for free. `hitl_required`/`hitl_proposal` flow
    through the existing generic HITL plumbing unchanged — `execute_create_ticket` needed zero
    changes. Design: `auto-sdlc/designs/B7-step4.md`. Tests:
    `tests/unit/test_mcp_agent_ticket_suggestion.py` (4 cases).
- **Two production concerns — checked against live code (2026-09-29), both already resolved:**
  - **B7a — silent live→mock fallback:** ☑ **NOT REPRODUCIBLE ON LIVE PATH.** Read every `except`
    branch in `sdlc-mcp-server/connectors/{jira,github,slack,confluence}_connector.py` (15 total) —
    zero fabricated/mock data on error. Reads return honest empty (`[]`/`None`) on failure; writes
    return `{"success": False, "error": ...}`. `registry.py::_build_connector` doesn't have a mock
    branch at all — if a connector is enabled but `is_available()` is false (no live creds), it
    **raises `RuntimeError` at startup**, real-or-crash, not real-or-mock-at-call-time. This
    described the pre-B7-migration `ai-sdlc-assistant/backend/connectors/` registry (same "stale
    narrative" pattern as B1/B2/B4/B5) — the live `sdlc-mcp-server` path never had this bug.
    **Real remaining gap (not the same bug):** silent **empty**-on-error (not mock-on-error) can
    still look like "genuinely zero results" to a caller — e.g. an auth-expired 401 on
    `get_blocked_tickets` returns `[]`, same shape as "no blockers exist." That's B7c's concern
    (fail-loud for live-required ops), not B7a's — tracked there, not duplicated here.
  - **B7b — MCP server has no auth:** ☑ **ALREADY BUILT.** `sdlc-mcp-server/server.py` wires
    `AuthSettings` (full OAuth 2.1: client registration, consent flow, `allowed_hosts` restricted to
    `127.0.0.1`/`localhost`/`host.docker.internal`) via `mcp.server.auth`; `auth/service_token.py`
    is the machine-to-machine path the backend actually uses (`secrets.compare_digest` — constant-time,
    avoids a timing side-channel on the secret). This was built in a prior session but never marked
    done here.
  - **B7c — fail-loud for live-required ops (user-spotted)  ☑ FIXED (2026-10-02):** when MCP is
    down/empty, the agent used to degrade to RAG-only unconditionally. Right for *knowledge*
    questions, wrong for *live-data* ones (ticket status/create, PR actions) — RAG-only could
    answer from a stale doc chunk instead of admitting it can't confirm current state. Fixed with
    a data-driven signal (P1: no keyword list) in `gather_via_tools()`:
    - **MCP server itself unreachable** (`get_mcp_tools()` raises or returns no tools): a cheap
      LLM classification call (`needs_live_data_classify` prompt, temperature 0.0) decides whether
      *this specific query* needed live data — the gather loop never got a chance to let the model
      request a tool itself here, so this is the one spot still asking an LLM directly rather than
      reading the model's own tool-call intent.
    - **Tools available but every attempted call errored** (timeout/auth/connection): no extra LLM
      call needed — the model already decided it needed that tool (it emitted the `tool_calls`),
      so "all attempts failed" is itself the live-data-needed-but-unavailable signal. Partial
      success (some calls ok) does NOT trip this — real data came back, nothing to disclaim.
    - New `ToolGatherResult.mcp_unavailable` flag; `MCPAgent.run()` checks it right after the
      gather step (before the domain/low-confidence guards, since a stale RAG chunk could otherwise
      answer a live-data question) and returns an honest "Live tools ... are currently unavailable"
      message instead of proceeding to RAG-only synthesis.
    - Pairs with B7a (no silent mock) — together: never fabricate, never guess from stale docs when
      the real answer needs a live system.
    - **Where:** `backend/mcp_client/tool_use.py` (`ToolGatherResult.mcp_unavailable`, `_needs_live_data`,
      `gather_via_tools`), `backend/agents/mcp_agent.py` (`run`, the new guard), `config/prompts.yaml`
      (`needs_live_data_classify`).
    - **Tests:** `tests/unit/test_tool_use_gather.py` (6 cases: MCP-down+live-data-query,
      MCP-down+knowledge-query, classifier-failure-defaults-safe, no-tool-call-attempted,
      all-attempts-failed, partial-failure-not-flagged); `tests/unit/test_mcp_agent_live_data_unavailable.py`
      (2 cases: short-circuits with the honest message, normal path unaffected).
  - **Docker:** `mcp-server` added as its own compose service (decoupled, one `docker compose up`); backend
    points at it via `MCP_SERVER_URL`. Rebuild needed after requirements change (`docker compose build
    backend mcp-server`).
- **MCP engineering-hardening — ☑ DONE:**
  - **B7d — Session/connection model ☑:** tool SCHEMAS fetched once via `tools/list` and cached
    (`_all_tools_cache`) + client singleton → no per-request re-listing. Per-call stateless streamable-HTTP
    session kept by design (a shared long-lived session is single-flight/unsafe under concurrent requests);
    `clear_tools_cache()` added for redeploys. `backend/mcp_client/client.py`.
  - **B7e — Parallel tool execution ☑ (= Phase 3.4):** a turn's tool calls now run via `asyncio.gather`
    under `Semaphore(4)` with per-call failure isolation; order preserved for valid ToolMessages.
    `backend/mcp_client/tool_use.py`.
  - **B7f — Tool result normalization ☑:** `_normalize_result` parses JSON-string / list-of-JSON-string
    tool output (e.g. `github_list_open_prs`) into clean dicts; `_to_text` pretty-prints for the prompt.
    Verified: list-of-JSON-strings→dicts, dict passthrough, plain string kept. `tool_use.py`.
- **Finding (from code):** no JSON-RPC, no stdio/Streamable-HTTP transport, no `mcp` SDK anywhere.
  Each "connector" is a **direct REST API client** (`httpx`) — e.g. `jira_connector.py` calls
  `api.atlassian.com` directly. `MCPRegistry` is a **plugin/adapter registry**, not an MCP host/client.
- **Consequence (ties to P1):** the **agent code decides which method to call**
  (`mcp.get("jira").get_blocked_tickets()`), so tool selection is **hardcoded by us**, not chosen by
  the LLM from tool descriptions. With many connectors this doesn't scale to "LLM picks the tool".
- **Real MCP would be:** an **MCP client** in our host that does `tools/list` (discovery) →
  the **LLM supervisor** picks tools by description → `tools/call`. Servers can be official remote ones:
  **Atlassian Rovo/Remote MCP Server** (Jira/Confluence, OAuth, `https://mcp.atlassian.com/v1/mcp`) and
  **GitHub official MCP Server**.
- **Decision needed (C6):** keep the typed REST-adapter (simple, reliable, but "MCP-inspired" not real
  MCP — risky since MCP is graded **Very High**) vs. add a real **MCP client + LLM tool-use** against
  official/self-hosted MCP servers. See explanation in chat.

### 🔴 B8 — RAG retrieval is lagging / noisy on the knowledge base  ◐ CAUSE 1+2 FIXED (2026-09-28)
- **Reproduced live** — query `Clean Code checklist` (manager): `agent=cross_source`,
  `strategy=degraded`, `confidence=0.357`. Answer was a **generic** checklist pulled from the wrong
  doc (**"Clean Code Best Practices"** deck), framed as delivery advice — **not** the actual
  **"Clean Code Checklist"** document.
- **Direct retriever probe:** top chunk (0.357) = "Clean Code Best Practices"; the real
  "Clean Code Checklist" doc chunk scored **0.034** (buried), and its content is fragmented/odd
  (e.g. "## Extra Code / 4 / I am not committing any confidential information").
- **Root causes:**
  1. ☑ **FIXED — Recall not triggered:** `_wants_full_document()` lived only in the legacy
     `cross_source_agent.py`, which `run_cross_source` stopped routing to once `MCPAgent` shipped (see
     B2/B5) — so on the **live path today, full-document recall didn't exist at all**, not even the old
     keyword-regex version (a "never lose an existing feature" gap from the B7 migration, not just a
     brittle regex). Fixed properly rather than re-porting the regex (which wouldn't have matched plain
     "Clean Code checklist" either — no "show/whole/full" verb): extracted the title-overlap logic
     `identify_document()` already used into `HybridRetriever._match_doc_title()`, and wired it directly
     into `retrieve_with_corrective_rag()` (what `MCPAgent` actually calls) as the recall-intent signal
     itself — a **>=2-word title match** on the first-pass chunks' candidate doc_titles triggers
     `retrieve_full_document()`, no separate keyword list (closes the P1 gap for this spot too). One
     retrieval call, no perf regression; `identify_document()` now delegates to the same helper
     (min_overlap=1, unchanged behavior for its own — legacy — callers).
  2. ☑ **FIXED (same change) — Big doc drowns small doc:** the title-overlap match already ignores
     chunk rerank rank/score — "Clean Code Checklist" (3-word overlap) beats "Clean Code Best Practices"
     (2-word overlap) regardless of which chunk had the higher reranker score.
  3. **Still open — Chunking/ingest quality:** the Checklist doc's own chunks are fragmented/odd
     independent of retrieval routing — needs a chunking pass, not a retrieval fix.
- **Where:** `backend/rag/retriever.py` `_match_doc_title` (new), `identify_document` (refactored to
  share it), `retrieve_with_corrective_rag` (recall check added). `mcp_agent.py` needed **no change** —
  it already calls `retrieve_with_corrective_rag` and already branches on `rag_strategy=="full_document"`
  for the token budget.
- **Tests:** `tests/unit/test_retriever.py` — `test_corrective_rag_recalls_full_document_on_strong_title_match`,
  `test_corrective_rag_skips_recall_on_weak_title_overlap` (2 new).
- **Fix direction (still open, cause 3):** recursive-512 rechunk of the Checklist doc + re-ingest;
  consider doc-aware retrieval boosting for the general noisy-retrieval case beyond named-doc recall.
- **Root cause narrowed (2026-10-04), not fixed — needs a live re-ingest to confirm:** queried the live
  Qdrant `sdlc_knowledge` collection directly (Docker/Qdrant was up this session). "Clean Code Checklist"
  has only 5 points for 1 real parent doc, from the **two separate phases** `ingestion_service.py
  ingest_confluence()` already runs per page: Phase 1 (page **body** text → `chunk_document`) produced
  a clean, correctly-formatted markdown table chunk (`| Sq No | Name | Category | ... |`, header intact).
  Phase 2 (the page's **PDF attachment**, same checklist content → `ingest_file` → `unstructured.io` →
  `chunk_from_elements`, which already isolates `Table` elements whole, never mid-split) produced a
  *different*, garbled chunk for the same table — plain run-on text with no pipe/header
  (`"...Code readability \n7 I have followed..."`) — meaning `unstructured.io` didn't detect this
  specific PDF's checklist as a `Table` element at all (fell through to `NarrativeText`/`Title` instead,
  which *does* get mid-split — exactly B8's reported symptom). Current chunker code is correct for every
  table it successfully detects; this is a **detection miss on one specific PDF**, not a chunking-logic bug.
  **Not fixed today:** no live Confluence credentials/running backend in this session to pull the actual
  PDF and confirm; fixing blind (e.g. guessing an `unstructured` strategy param) would violate "reproduce
  the failure first" with no way to verify here. **Next step (needs live Confluence access):** re-run
  admin `Clear All` + `Ingest from Confluence` for project SDLC. If the PDF-attachment phase still
  produces a header-less table chunk afterward, that's the reproducible case to fix (try `unstructured`'s
  `hi_res`/table-extraction strategy for this file, or a markdown-table reconstruction fallback when a
  `Table`-shaped block lands in `NarrativeText`). Also worth asking: Phase 1 (page body) and Phase 2 (PDF
  attachment) ingest the **same checklist content twice** under one `doc_title` — low urgency today, but
  if the page body's table render is reliably clean, Phase 2 may be redundant for pages whose PDF is just
  an export of the same body.

### 🟠 E8 — HITL approval UX: per-action scopes (Approve / Approve-all / Reject)  ☑ DONE (2026-10-06)
- **User ask:** risky actions must always ask; low-risk repeats (Slack notify) could offer a
  "yes to all (this conversation)" so the user isn't re-prompted every time.
- **Research-backed pattern (production HITL):** hard **propose → commit** separation (we have this);
  human review reserved for **risky / irreversible / external** actions; full **audit trail**;
  approvals scoped at different levels (per-action vs session). Re-confirmed live (2026-10-06): 2026
  HITL approval-gate guidance names this exact pattern — safe actions run freely, risky actions get a
  **one-time "always allow for this session"** opt-in, destructive actions always ask — to avoid
  "approval fatigue" (reviewers rubber-stamping once every action needs the same click).
- **Decision matrix (shipped as-is):**
  | Action | Risk | Options offered |
  |---|---|---|
  | Create / edit / delete ticket | high / irreversible | **Approve · Reject** (no approve-all) |
  | Assign reviewer · Approve/Reject PR | high | **Approve · Reject** |
  | Release GO | high | **Approve · Reject** (+ role gate) |
  | Slack / Teams **notify** (`send_slack`) | low / reversible-ish | **Approve · Approve-all (this conversation) · Reject** |
- **Shipped:**
  - `hitl-card` (Angular): a 3rd button, shown only when the proposal's action type is on
    `HitlService`'s `APPROVE_ALL_ELIGIBLE` set (today: `send_slack` only). Clicking it executes once
    (identical result to Approve) and remembers the opt-in; every later proposal of that action type
    in the same tab's conversation auto-resolves on mount — no click, no card shown — with a small
    "(Auto-approved for this conversation)" note on the result.
  - **Scope lives client-side, not in Redis/HITLManager:** considered a backend session flag
    (mirroring `HITLManager`'s own Redis+fallback pattern) but rejected — it would require
    `check_hitl` (the orchestrator node) to duplicate `hitl.py`'s entire action-dispatch `if/elif`
    chain (role checks, MCP calls, episodic memory, session-store updates) to auto-execute without a
    frontend round-trip, a correctness risk for zero behavioral gain over just letting the frontend
    fire the existing `/api/hitl/approve` call itself. Reload / new session → opt-in is gone, matching
    the research pattern's **session**-scoped tier (not "always allow forever").
  - `HITLRequest.remember` (bool, default False) + `ChatResponse.hitl_action_type` (new) — audit-trail
    marker only; `remember` never affects authorization (`require()` still runs every time) or what
    executes. `hitl.py`'s episodic-memory record now notes "(remembered for this session)" when set.
  - Eligibility list is a static UX/policy constant (which action types are low-risk), not a
    probabilistic agent decision — P1 governs agent decisions (routing/retrieval/duplicate-detection),
    not this kind of fixed product policy; a 1-entry YAML config would be ceremony over substance here.
  - Design doc: `auto-sdlc/designs/E8.md`.
- **Tests:** `tests/unit/test_chat_hitl_action_type.py` (3), `tests/unit/test_hitl_approval_event_text.py`
  (3), `frontend-angular/src/app/chat/hitl-card/hitl-card.spec.ts` (5 — first spec for this component).
- **Deferred, not dropped:** "Add/edit comment" never shipped as a HITL action type at all yet (ties
  to E6's own open "edit/delete comment" gap) — nothing to add to the eligible set until that exists.
  `agents.yaml` per-action risk config wasn't needed — the eligible set is small enough (1 entry) that
  a Set literal in `hitl.service.ts` is the whole implementation; revisit if the set grows past a
  handful of entries or a second consumer needs the same policy.

### 🟠 B9 — Parallel calls exist, but tool selection/chaining is static (no agentic loop)  ◐ LACK 1 FIXED (verified 2026-10-06)
- **What we HAVE (good):** parallel multi-connector calls — every agent uses `asyncio.gather`, and
  `MCPRegistry.call_parallel()` runs N calls under an `asyncio.Semaphore(3)` with per-call failure
  isolation (`return_exceptions=True`). Concurrency is production-shaped.
- **Lack 1 — ☑ NOT REPRODUCIBLE on the generalist path (verified 2026-10-06), real for 4 specialists
  by design:** this entry predates B7 Step 2 (`gather_via_tools()`), which already IS a `bind_tools`
  ReAct loop — `for _ in range(max_iters)`: call the model, append its tool calls' results as
  `ToolMessage`s to the running `messages` list, call the model **again** with that updated history,
  repeat up to 5 rounds, stop when the model emits no more tool calls. That is exactly "let the LLM
  see a tool result and decide the next tool" — this entry was accurate when written, stale once B7
  Step 2 shipped (same pattern as B1/B2/B4/B5/B7a/B7b/E3/E4). `MCPAgent` (the live generalist) and
  `NotifyAgent`'s dynamic-fallback path both route through `gather_via_tools`. **Still true, by
  design, not a bug:** the other 4 specialists (ticket/risk/pr_review/release_readiness) call
  `call_mcp_tool(...)` directly with a fixed set of calls — documented already under B7 Step 4's
  "Design distinction" note (generalist = LLM picks tools; specialists = fixed, known tool needs).
  No reproducible symptom for the specialists needing dynamic chaining — not converting speculatively.
  **Test:** `tests/unit/test_tool_use_gather_chaining.py` — proves genuine 2-round chaining (round 2's
  model call receives round 1's actual tool *result* as a message, and picks a different tool
  informed by that result's content), closing the one real gap in the existing gather tests (which
  only ever exercised a single round).
- **Lack 2 — still open, no reproducible need:** agent-to-agent communication — agents are graph
  nodes; only one runs per query. Cross-agent collaboration would use a supervisor-worker topology or
  **Google A2A** (agent↔agent, vs. MCP's agent↔tool). Not building speculatively — no query pattern
  observed today needs one agent to consult another mid-turn.

### 🟠 E9 — Frontend explainability / decision trace labels  ☑ DONE (2026-09-27)
- **User ask:** while processing and in the answer, show **what was called, the scores, why each thing
  was chosen** — incl. **which RAG chunks were used and why** (relevance score).
- **Shipped:** `classifier.llm_classify()` now returns `(agents, reason)` instead of discarding the
  LLM's routing reason; `classify_intent` puts it on `SDLCState.routing_reason`. `chat.py::_build_trace()`
  assembles it with `agent_payloads[*].structured["mcp_calls"/"rag_chunks"]` (already computed by
  `MCPAgent`, previously dropped before the response) into `ChatResponse.trace` — `None` when there's
  nothing to explain (HITL proposal / blocked / no-evidence refusal), so no empty panel ships.
  Angular: a native `<details>`/`<summary>` "Why this answer?" panel under the existing meta-chip row
  (`chat.html`/`chat.css`/`chat.ts`) — no new dependency, reuses the established chip palette.
- **Design doc + mockup:** `auto-sdlc/designs/E9.md` / `E9.html` (Stitch/Claude Design unavailable in
  this environment; hand-built static mockup against the existing chat.css palette instead).
- **Tests:** `tests/unit/test_classifier_routing_reason.py`, `tests/unit/test_chat_trace.py` (backend);
  `frontend-angular/src/app/chat/chat.spec.ts` (Angular, first spec for this component).
- **Where:** `backend/orchestrator/classifier.py`, `state.py`, `nodes.py`; `backend/api/routes/chat.py`;
  `backend/api/models/schemas.py`; `frontend-angular/src/app/chat/*`.

---

### 🟠 E10 — Standing regression test/query suite + automation  ☐
- **Created `TEST_SUITE.md`** — per-agent query groups (simple→advanced→edge→negative) + cross-cutting
  (cross-connector, memory, persona, RAG, guardrails), marked ✅current / ⏳pending / ⚠️known-bug.
- **Rule:** every new feature adds a case here + a golden row in `data/eval_set.json`; run before/after
  every change to catch regressions.
- **Automation follow-up:** wire **DeepEval/Ragas + LangSmith** to run evals on every PR with score
  thresholds (fail on regression). Today we have a lightweight homegrown eval (`eval_set.json` +
  `metrics.py` + admin `04_evaluation`).

### 🔴 B10 — Memory is half-dead: retrieved/stored but not injected into prompts  ◐ PARTIAL (updated 2026-10-04)
Traced every layer end-to-end (write → retrieve → **inject into LLM prompt**):
| Layer | Stored? | Retrieved? | **Actually fed to LLM?** | Verdict |
|---|---|---|---|---|
| Conversational / session (SQLite `recent_messages`) | ✅ | ✅ | ✅ **MCPAgent** (`_format_history`, the live generalist path) — legacy `cross_source_agent` also has it but is unrouted | live-path fixed |
| Conversation **summary** (LLM-compressed) | — | ✅ computed (`_summarize_turns`, an LLM call) | ✅ **MCPAgent** injects `state.conversation_summary` as its own prompt section | live-path fixed |
| Semantic facts (Qdrant, 21 stored) | ✅ written every answer | ✅ `retrieve_facts` every query → `state.semantic_context` | ✅ **MCPAgent** injects it (`## Project Knowledge` section, sanitized + XML-wrapped) | live-path fixed |
| Episodic (Qdrant, HITL actions) | ✅ on approve | ✅ MCPAgent, historical/mixed intent only | ✅ | fixed |
| Redis semantic cache | — removed entirely (B6) | — | n/a | n/a |
| `ContextBuilder` (7-slot tiktoken budget) | — | — | ❌ **never called** by any agent/node — MCPAgent builds its prompt inline instead | dead module (harmless — superseded, not the injection gap) |
- **Correction (2026-09-28):** the table above was stale — it predates `MCPAgent` (the agent
  `run_cross_source` actually routes to today; legacy `cross_source_agent.py` is dead code on the live
  path, see B2/B5). Re-read `mcp_agent.py::run()`: it already injects `recent_messages`, `conversation_summary`,
  AND `semantic_context` into its prompt (lines ~255-327). **The one real remaining gap was the 5
  specialist agents** (ticket/risk/pr_review/release_readiness/notify) — fixed-tool-need agents that never
  saw `state.recent_messages` at all.
- **Fixed today — `ticket_agent.py`:** write-intent queries with no explicit ticket ID ("reassign **that
  ticket** to alice", "log a note on **it**: ...") matched `assign_match`/`comment_match` but had no
  `ticket_id_match`, so the code silently fell through every branch into the ticket **CREATE** flow —
  proposing a bogus new ticket instead of acting on the one just discussed. Added
  `_last_ticket_id_from_history()` (resolves against `state.recent_messages`, newest turn first — same
  rule MCPAgent's prompt already documents) and a shared `_ask_for_ticket_id()` clarification response for
  when nothing resolves (ask, don't guess — matches the existing edit-intent pattern). All three write
  branches (assign/edit/comment) now use one `resolved_ticket_id`.
- **Fixed today (2026-10-04) — `pr_review_agent.py`:** same pronoun gap, confirmed real (not
  speculative): "approve it" / "assign alice as reviewer" right after "review PR-5", with multiple
  PRs open, hit the ambiguity guard and asked "which PR did you mean?" even though the answer was
  the PR just discussed. Added `_last_pr_id_from_history()` (identical newest-turn-first,
  response-before-query rule as ticket_agent's) as a fallback for `_query_target_pr_id()` — an
  explicit PR id in the current query still always wins. This also fixes the related "deep review
  vs list" branch for free: "what's the status of that PR" now reviews the one PR instead of
  falling through to `broad_query` and listing all of them.
- **Still open — risk / release_readiness / notify agents:** checked for the same shape of bug
  (a pronoun referring to one specific tracked entity) and found none to fix — both risk and
  release_readiness are whole-sprint report generators with no single target to resolve, and
  notify's only entity-like field (channel) is either explicit (`#channel`) or LLM-extracted from
  the current query, with no pronoun pattern observed. Not fixing speculatively (no reproducible
  symptom) — re-open this line if a concrete case shows up.
- **Where:** `orchestrator/nodes.py` `retrieve_memory_context` (produces the fields); `agents/mcp_agent.py`
  (consumes all 3, already fixed); `agents/ticket_agent.py` (history-resolution added 2026-09-28);
  `agents/pr_review_agent.py` (history-resolution added 2026-10-04).
- **Tests:** `tests/unit/test_ticket_agent_history_resolution.py` (4 tests),
  `tests/unit/test_pr_review_agent_history_resolution.py` (new, 4 tests). See also
  `TEST_SUITE.md` §B2 (MEM-1…4).

### 🟠 E11 — Automated suite runner + observability (eliminate manual testing)  ☐
- **Goal:** one command fires all `TEST_SUITE.md` queries and **asserts machine signals** (`.agent`,
  `.strategy`, `.confidence`, `.faithfulness`, `.hitl_required`, status codes) → no eyeballing per case.
- **We already expose:** those JSON fields + `eval_results.jsonl` + admin `04_evaluation` + rich logs
  (corrective-RAG, LOW FAITHFULNESS, injection BLOCKED, intent routing).
- **Missing:** (a) a per-response **trace** (tools called, chunk scores, routing `reason`) — ties to E9;
  (b) the **runner script** (pytest or a CLI) that executes the suite and checks thresholds (DeepEval/
  Ragas/LangSmith for the production version) — ties to E10.
- **System levels documented (for tests):** confidence tiers high0.75/med0.45/low/no-evidence0.20;
  corrective RAG = **1 retry** (`first_pass→corrective→degraded`, no loop); security = defense-in-depth
  (injection-403, role-403/409, HITL, env-creds, 10/min limit) — mapped to **OWASP LLM Top 10**.

### 🟠 E12 — Product-grade motion system (GSAP + native Angular)  ◐ SLICE 2 DONE (2026-10-04)  **(requested)**
The UI has almost no motion; enterprise chat products use it to show state (streaming, thinking, tool calls, approvals).
- **Two tiers, one system.** Simple enter/leave and hover/focus transitions use native Angular `animate.enter` /
  `animate.leave` + CSS (migrate off the legacy `@angular/animations` package where it's used). **GSAP** (`gsap` npm)
  is only for what CSS can't do well: timelines, Flip layout transitions (message list, trace panel expand),
  SplitText reveals, and ScrollTrigger in admin dashboards. Check GSAP's current licence and Angular's animation
  API in their docs before starting.
- **Slice 1 shipped:** confirmed live (`angular.dev/guide/animations`, fetched 2026-09-29) `animate.enter`/
  `animate.leave` are stable since Angular v20.2 (we're on 21.2) and are the team-recommended replacement for
  `@angular/animations` — audited the whole `frontend-angular/src` tree and found **zero** legacy
  `trigger()`/`@angular/animations` usage to migrate (only `provideAnimationsAsync()` for Material internals,
  unrelated). Confirmed GSAP is **100% free including all plugins** since Webflow's April 2025 sponsorship — no
  licence blocker for a later slice. Shipped: `styles.css` motion tokens (`--motion-fast`/`--motion-base`/
  `--motion-ease-out`) + a global `prefers-reduced-motion` kill-switch (one rule covers every current/future
  animation, not a per-component check); `.bubble` entrance (chat.html/css, short+subtle — high-frequency
  per `emil-design-eng` judgement); a streaming caret shown only while the last assistant message is actively
  streaming; HITL card entrance (more deliberate — occasional, not high-frequency). Design doc:
  `auto-sdlc/designs/E12.md`. Tests: 3 new cases in `chat.spec.ts` for the caret's show/hide logic.
- **Audit correction (2026-10-04) — toast/snackbar was already DONE, just not credited:** every admin
  write action (`rag-manager`, `memory`, `mcp-servers`, `sessions`, `config-viewer`) already shows
  `MatSnackBar` feedback on success/error (ingest results, clear-all, server add/toggle/delete, config
  reload, etc.) — same stale-backlog pattern as B1/B2/B4/B5/B7a/B7b. No code change needed; corrected
  here so a future run doesn't rebuild it.
- **Slice 2 shipped (2026-10-04) — sidebar collapse:** `admin.html`/`admin.ts` — a toggle button in
  `mat-sidenav-content` (always reachable regardless of sidenav state) flips `[opened]` on the existing
  `mat-sidenav`. Zero new code for the transition itself — `mode="side"` + `[opened]` is MatSidenav's
  own built-in open/close animation (same CDK overlay/animation internals Material already uses
  everywhere else in this app), and the global `prefers-reduced-motion` kill-switch from Slice 1 already
  covers it (one `*` rule, no per-component override needed). No GSAP/MotionService needed here either.
  **Tests:** `admin.spec.ts` (new — 2 cases: starts expanded, toggle flips state).
  **Verification gap (environment, not code):** this session's shell resolved Node v20.12.2
  (`nvm4w`), below Angular CLI 21's v20.19 minimum, so `ng test`/`ng build` could not be run here —
  self-reviewed instead via `tsc --noEmit` (clean) + manual read-through of the binding names against
  `admin.ts`. Backend (126) + MCP-server (8) gates ran directly and are green. Prior runs (through
  2026-10-02) recorded `ng test`/`ng build` passing, so the actual `run-daily.ps1` PowerShell process
  likely resolves a different/working Node on PATH than this interactive shell did — **Needs Dixit**
  to confirm, same category as the 2026-10-02 Docker-engine note.
- **Still open (deliberately deferred, not dropped):** GSAP + `MotionService` (nothing yet needs
  timelines/Flip/SplitText/ScrollTrigger — adding the dependency now would sit unused); skeleton
  loaders; admin chart entry; `--motion-slow` token (add with the first modal/drawer-class animation);
  bundle-growth check (moot until GSAP lands).
- **Shared tokens:** `src/styles` motion tokens (durations 120/200/320ms, 2–3 easings) + one `MotionService` that
  wraps GSAP. Components never import gsap directly; run GSAP outside the Angular zone and kill tweens in `ngOnDestroy`.
- **Where motion goes:** streaming token caret + "thinking" state; tool-call/trace step reveal (E9 panel);
  HITL approval card in/out; toast/snackbar; sidebar collapse; skeleton loaders; admin chart entry.
- **Accessibility (non-negotiable):** honour `prefers-reduced-motion` (GSAP `matchMedia` → instant), no motion that
  blocks input, nothing flashing more than 3×/s.
- **Acceptance:** motion tokens used everywhere (no ad-hoc durations); reduced-motion spec test per animated component;
  no layout jank on a 200-message thread; `ng build` bundle growth ≤ 60 kB gzip.

## F. Reference implementations to model on (external)
- **arXiv — "Agentic AI in the SDLC: Architecture, Empirical Evidence"** — 6-layer A-SDLC reference architecture.
- **CodinjaoftheWorld/agentic-sdlc-langgraph** (GitHub) — full SDLC pipeline on LangGraph + ChatGroq + HITL — closest stack to ours.
- **Agentic RAG + MCP step-by-step** (Vishal Mysore; Omar Santos / becomingahacker) — agent + MCP client + vector store reference impls.
- **Microsoft multi-agent reference architecture** — orchestrator + classifier(intent routing) + agent registry + MCP server (mirrors our shape).
- **Weaviate — "What is Agentic RAG"** + **InfoQ hierarchical agentic RAG** — corrective/adaptive RAG patterns.
- **Protocols:** Anthropic **MCP** (agent↔tool) + Google **A2A** (agent↔agent) — the two emerging standards.

---

## G. Out-of-the-box standards audit & restructure verdict (2026-06-27)

> Each pillar checked against current industry standards/examples. **Verdict legend:**
> 🟢 keep (matches standard) · 🟡 restructure (right idea, wrong/partial impl) · 🔴 replace.
> **Bottom line first:** we do **NOT** need a greenfield rewrite — the skeleton (LangGraph + FastAPI +
> Qdrant + Redis + agents + HITL) *is* the 2026 reference stack. We need **targeted restructuring of 4
> pillars + 2 additions**, then finish the role-based CRUD. "The framework is not where differentiation
> lives in 2026 — build only if inventing a new paradigm." We're not; so refactor, don't rewrite.

| Pillar | Our impl | Industry standard (2026) | Verdict | Gap → action |
|---|---|---|---|---|
| **Orchestration** | LangGraph stateful graph + HITL | LangGraph is the top framework for durable state + approval checkpoints | 🟢 keep | none — this is the right choice |
| **Routing** | LLM supervisor (LLM-first) + keyword fallback | LLM/classifier intent routing | 🟢 keep | minor: convert remaining regex gates (P1) |
| **MCP** | direct REST clients in a registry ("MCP-inspired") | **real MCP** is THE 2026 standard, the one layer that transfers everywhere; official Atlassian/GitHub servers | 🔴 replace | **B7** — add real MCP client + LLM tool-use; use official servers |
| **Agentic tool use** | fixed per-agent calls (parallel OK) | LLM tool-use loop / supervisor-worker; A2A for agent↔agent | 🟡 restructure | **B9** — add tool-selection/chaining loop |
| **RAG — chunking** | parent-child (good) but fragmented chunks on some docs | parent-child ✓; **recursive 512-token** is the strong default (69–90% recall); semantic chunking fragments | 🟡 restructure | **B8** — fix chunking (recursive ~512), re-ingest, kill fragments |
| **RAG — embeddings** | all-MiniLM-L6-v2 (384d, free) | fine baseline; **BGE-M3** (dense+sparse+multivector, 8k) is the modern open upgrade | 🟢 keep / optional | optional: BGE-M3 if recall matters |
| **RAG — retrieval** | hybrid BM25+vector+rerank+corrective(1 retry) | hybrid + rerank ✓; CRAG/Self-RAG/Stop-RAG add value-based stop | 🟢 keep | recall-intent must be probabilistic (P1/B8) |
| **Memory** | 4 layers but ~1.5 actually injected | Redis(working)+vector(semantic)+Postgres(history); Mem0/LangMem; extract→consolidate→store→**inject** | 🔴 restructure | **B10** — actually inject; consider Mem0/LangMem |
| **Cache** | Redis response cache | cache RAG/static only, never live MCP data | 🔴 replace | **B6** — remove or scope to RAG-only |
| **Evaluation** | homegrown faithfulness/relevancy/precision/recall/correctness | 3 tiers: PR checks → nightly regression → prod monitoring. "89% have observability, only 52% have evals — quality dies there" | 🟡 restructure | **E10/E11** — automate + CI thresholds (DeepEval/Ragas) |
| **Observability** | logs + eval_results.jsonl + admin page | distributed **tracing** (LangSmith/Langfuse/Arize): tools, chunk scores, routing reason | 🟡 add | **E9/E11** — per-response trace + tracing backend |
| **Security/guardrails** | injection-403, role gates, HITL, env creds, rate-limit | OWASP LLM Top 10; **authz at tool-execution layer, not output**; defense-in-depth | 🟢 keep / extend | **E1** — finish role→action gates at execution |
| **HITL** | propose→commit, approve/reject on Redis | propose/commit ✓; scoped approvals; audit trail | 🟢 keep / extend | **E8** — add approve-all-this-session for low-risk |
| **Frontend** | Angular chat + chips | thin client is fine (UI graded Low) | 🟢 keep | **E9** — add "why this answer" trace panel |

### Target ("out-of-the-box") architecture
```
Host (FastAPI) ── LangGraph supervisor (LLM-first routing)
  ├─ MCP CLIENT (langchain-mcp-adapters) → tools/list → LLM tool-use loop
  │     ├─ Atlassian/GitHub official MCP servers (or our own MCP server wrapping connectors)
  │     └─ parallel calls, concurrency cap, HITL gate at EXECUTION layer
  ├─ RAG: recursive-512 parent-child + hybrid + rerank + corrective; probabilistic recall
  ├─ Memory: working(Redis) + semantic(Qdrant, injected) + episodic + summary  (Mem0/LangMem)
  ├─ Eval: faithfulness/precision/recall/correctness  → CI thresholds (DeepEval/Ragas)
  └─ Observability: per-response trace + LangSmith/Langfuse
```

### Phased migration (no rewrite — restructure in order)
1. **P0 (correctness/demo):** B6 cache off · B1 stale-data · B8 chunking/recall · B10 inject memory · B2/B5 one canonical ticket path.
2. **P1 (the headline gaps):** B7 real MCP client + tool-use loop (B9) · E9 trace panel · E10/E11 automated eval+runner.
3. **P2 (role-based assistant):** E1–E7 CRUD + permissions + notifications · E8 HITL scopes.
4. **P3 (polish/scale):** BGE-M3 embeddings · Mem0/LangMem · CRAG value-based stop · Langfuse tracing.

### Verdict in one line
**Restructure 4 pillars (MCP, Memory, RAG-chunking, Cache) + add 2 (real tool-use loop, observability/eval automation) + finish role-CRUD. Keep the LangGraph/FastAPI/Qdrant skeleton. No full rewrite.**

Sources: [AI Agents Stack 2026 — O'Reilly](https://www.oreilly.com/radar/the-ai-agents-stack-2026-edition/) ·
[Best AI agent frameworks — LangChain](https://www.langchain.com/resources/ai-agent-frameworks) ·
[Best chunking strategies 2026 — Firecrawl](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) ·
[Corrective RAG — Meilisearch](https://www.meilisearch.com/blog/corrective-rag) ·
[OWASP Top 10 for LLM Apps](https://owasp.org/www-project-top-10-for-large-language-model-applications/) ·
[Mem0 — long-term memory (arXiv)](https://arxiv.org/pdf/2504.19413)
