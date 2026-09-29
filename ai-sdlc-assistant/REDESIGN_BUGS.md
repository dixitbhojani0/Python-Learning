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
- **E3 — Status queries return full detail:** status, assignee, priority, **latest comment**,
  effort/ETA — small or big. ☐
- **E4 — Ticket comments = source of truth** for "how big / how long", so stakeholders get real
  expectations instead of assumptions (directly supports B3). ☐
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
  - **Still TODO:** give MCPAgent write-intent → HITL proposal (restores the duplicate-ticket *suggestion*).
- **Two production concerns surfaced (fix in a later step, not silently now):**
  - **B7a — silent live→mock fallback:** connectors return mock data when a live API errors (e.g.
    `jira_connector` on HTTP 400/404). Hides outages, likely source of B2's phantom SDLC-1043. Standard:
    explicit `live|mock` mode per connector; in `live`, errors propagate (don't fabricate). ☐
  - **B7b — MCP server has no auth:** fine on 127.0.0.1, but exposing to other machines/apps needs a
    bearer token / OAuth (MCP spec supports it). Add before binding to 0.0.0.0. ☐
  - **B7c — fail-loud for live-required ops (user-spotted):** when MCP is down/empty, the agent degrades
    to RAG-only. Right for *knowledge* questions, WRONG for *live-data* ones (ticket status/create, PR
    actions) — RAG-only fabricates from stale docs or dodges. Standard = detect "needs live tools" and
    return an honest "live tools unavailable" instead of RAG/mock. Pairs with B7a (no silent mock). ☐
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

### 🟠 E8 — HITL approval UX: per-action scopes (Approve / Approve-all / Reject)  ☐
- **User ask:** risky actions must always ask; low-risk repeats (Slack notify) could offer a
  "yes to all (this conversation)" so the user isn't re-prompted every time.
- **Research-backed pattern (production HITL):** hard **propose → commit** separation (we have this);
  human review reserved for **risky / irreversible / external** actions; full **audit trail**;
  approvals scoped at different levels (per-action vs session).
- **Proposed decision matrix:**
  | Action | Risk | Options to offer |
  |---|---|---|
  | Create / edit / delete ticket | high / irreversible | **Approve · Reject** (no approve-all) |
  | Assign reviewer · Approve/Reject/Merge PR | high | **Approve · Reject** |
  | Release GO | high | **Approve · Reject** (+ role gate) |
  | Slack / Teams **notify** | low / reversible-ish | **Approve · Approve-all (this conversation) · Reject** |
  | Add/edit comment | medium | **Approve · Reject** (approve-all optional) |
- **Where:** `hitl-card` (Angular) for the 3rd button; `hitl.py` + HITLManager for a session-scoped
  "auto-approve this action type" flag; `agents.yaml` per-action risk level.

### 🟠 B9 — Parallel calls exist, but tool selection/chaining is static (no agentic loop)  ☐
- **What we HAVE (good):** parallel multi-connector calls — every agent uses `asyncio.gather`, and
  `MCPRegistry.call_parallel()` runs N calls under an `asyncio.Semaphore(3)` with per-call failure
  isolation (`return_exceptions=True`). Concurrency is production-shaped.
- **What we LACK:**
  1. **Dynamic tool selection/chaining** — no `bind_tools` / ReAct loop. The agent runs a **fixed**
     set of calls then reasons once; it never lets the LLM see a tool result and **decide the next
     tool**. (Ties to B7 + P1 — selection is hardcoded.)
  2. **Agent-to-agent communication** — agents are graph nodes; only one runs per query. Cross-agent
     collaboration would use a supervisor-worker topology or **Google A2A** (the agent-to-agent
     protocol; MCP is agent↔tool, A2A is agent↔agent).
- **Fix direction:** with real MCP + LLM tool-use (B7), the supervisor can iteratively pick/chain tools
  across connectors (parallel where independent). Consider supervisor-worker multi-agent if needed.

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

### 🔴 B10 — Memory is half-dead: retrieved/stored but not injected into prompts  ◐ PARTIAL (updated 2026-09-28)
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
- **Still open:** risk / pr_review / release_readiness / notify agents still don't consume
  `recent_messages`/`semantic_context` — lower priority than ticket_agent (they're mostly single-shot
  read/report queries, not multi-turn "that PR"/"that release" follow-ups) but the same gap.
- **Where:** `orchestrator/nodes.py` `retrieve_memory_context` (produces the fields); `agents/mcp_agent.py`
  (consumes all 3, already fixed); `agents/ticket_agent.py` (history-resolution added 2026-09-28);
  `agents/{risk,pr_review,release_readiness,notify}_agent.py` (still don't consume history).
- **Tests:** `tests/unit/test_ticket_agent_history_resolution.py` (new, 4 tests). See also
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

### 🟠 E12 — Product-grade motion system (GSAP + native Angular)  ☐  **(requested)**
The UI has almost no motion; enterprise chat products use it to show state (streaming, thinking, tool calls, approvals).
- **Two tiers, one system.** Simple enter/leave and hover/focus transitions use native Angular `animate.enter` /
  `animate.leave` + CSS (migrate off the legacy `@angular/animations` package where it's used). **GSAP** (`gsap` npm)
  is only for what CSS can't do well: timelines, Flip layout transitions (message list, trace panel expand),
  SplitText reveals, and ScrollTrigger in admin dashboards. Check GSAP's current licence and Angular's animation
  API in their docs before starting.
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
