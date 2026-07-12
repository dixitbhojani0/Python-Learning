# AI SDLC Assistant — Demo Plan & Acceptance Checklist

Run these queries live, one per row. Switch the logged-in **role** (token) where noted.
"Expected output" is what graders should see; "Proves" maps to the assignment's
weighted criteria. Tick the box after you verify it live.

> Tip: before the demo, run every query once to warm the semantic cache (Groq quota safety).

---

## Scenario 1 — Delivery risk + root cause  *(role: MANAGER)*
- **Ask:** `Are we at risk of missing the sprint? Why?`
- **Routes to:** `risk_agent`  → `backend/agents/risk_agent.py`
- **Expected output:** A risk verdict (e.g. AT RISK / ON TRACK) **with a named root cause**
  (blocked ticket, failing CI, stalled PR) drawn from Jira + RAG — *not* invented.
  Manager-framed language. Sources chips show jira / docs.
- **Proves:** Agentic reasoning (High), RAG (High), Problem Understanding (High).
- [ ] Verified

## Scenario 2 — Developer ticket → suggested solutions  *(role: DEVELOPER)*
- **Ask:** `SDLC-5 has a bug, what could be the fix?`
- **Routes to:** `ticket_agent`  → `backend/agents/ticket_agent.py`
  (ticket-ID fast-path in `classifier.py:7`)
- **Expected output:** Pulls SDLC-5 details from Jira MCP, checks for duplicates,
  suggests concrete fixes grounded in ADR/standards docs. Developer-framed.
  If duplicate exists → shows existing ticket, **no** create prompt.
- **Proves:** MCP usage (Very High), RAG (High), Problem Understanding.
- [ ] Verified

## Scenario 3 — PR HITL workflow — assign reviewer AND approve  *(role: DEVELOPER, then MANAGER)*
- **Routes to:** `pr_review_agent`  → `backend/agents/pr_review_agent.py`
- **3a — Assign reviewer:** Ask `Review the open PRs and assign a reviewer.`
  → review card + Approve/Reject. **Approve** → reviewer assigned via GitHub MCP
  (`assign_reviewer`). **Reject** → cancelled.
- **3b — Approve the PR:** Ask `Approve PR-49.` (also: "merge", "sign off", "lgtm")
  → review card + Approve/Reject. **Approve** → PR marked **APPROVED** via GitHub MCP
  (`approve_pr`; does NOT merge). **Reject** → cancelled.
- **Expected:** the query verb decides the action — "review/assign" → assign reviewer,
  "approve/merge" → approve PR. Re-ask as MANAGER → summary line changes, table identical.
- **Proves:** Human-in-the-loop (focus area), MCP **write** actions (two of them), role adaptation.
- **Note for graders:** reviews PR *metadata vs policy/standards docs* — not line-by-line diff
  review (not in scope). Approve approves the PR; merge stays a manual GitHub action by design.
- [ ] Verified assign (Approve/Reject)   [ ] Verified approve-PR (Approve/Reject)

## Scenario 4 — Business asks status without the PM  *(role: STAKEHOLDER)*
- **Ask:** `What's the status of the dashboard feature?`
- **Routes to:** `cross_source_agent`  → `backend/agents/cross_source_agent.py`
- **Expected output:** Synthesized status across Jira + Slack(+Teams) live data and sprint
  docs (RAG), written in **business language** (no jira jargon, no invented %).
  Source chips show multiple sources.
- **Proves:** Cross-source synthesis, MCP (Very High), persona adaptation, Problem Understanding.
- [ ] Verified

## Scenario 5 — Stakeholder notification  *(any role)*
- **Ask:** `Notify the team in #engineering-manager about the sprint status.`
- **Routes to:** `notify_agent`  → `backend/agents/notify_agent.py`
- **Expected output:** Confirms a message was sent to the **#engineering-manager** channel
  (LLM-extracted channel + composed message). Runs on Slack mock.
- **Proves:** MCP tool action, agent-to-tool interaction.
- [ ] Verified

## Scenario 6 — RAG depth (full-document recall + images)  *(any role)*
- **Ask:** `Show me the clean code checklist.`
- **Routes to:** `cross_source_agent` → full-document recall path
  (`_wants_full_document` → `retriever.retrieve_full_document`)
- **Expected output:** The **complete** checklist reassembled in order (not a vague summary),
  plus any embedded document images rendered below the answer.
  Higher token budget kicks in here (`response_full_document`).
- **Proves:** RAG quality / retrieval strategy (High) — the "not just prompting" requirement.
- [ ] Verified

## Scenario 7 — Out-of-scope guardrail  *(any role)*
- **Ask:** `What is the capital of France?`
- **Expected output:** A short honest "I don't have related project details" reply —
  **no** fabricated sprint status. (Relevance gate + anti-fabrication persona prompts.)
- **Proves:** Faithfulness / no hallucination — strong credibility signal.
- [ ] Verified

## Scenario 8 — Corrective / Adaptive RAG loop (show it self-correct)  *(any role)*
- **Ask (verified live):** `what should I tick off before calling my code clean?`
  (oddly phrased on purpose — first-pass retrieval is weak, the LLM rewrites it,
  second pass hits the Clean Code Checklist doc).
- **Backup queries (also verified to recover):**
  - `what are the rules before we bump the version number?`
  - `how do we decide who looks over someone's code changes?`
- **Watch the backend logs** — you will see, in order:
  ```
  HybridRetriever: low confidence (0.206) — triggering corrective RAG
  HybridRetriever: reformulated query: '...'
  HybridRetriever: corrective RAG RECOVERED — confidence 0.206 → 0.617 (strategy='corrective')
  ```
- **Proves:** Multi-step reasoning / adaptive RAG (High). Verified live: 0.206 → 0.617.
- **How many times does it loop?** Exactly **one** retry — first pass, then one LLM-rewritten
  retry. If still < 0.45 it stops and returns `degraded` (best of the two). No infinite loop.
- [ ] Verified (log shows the loop)

## Scenario 9 — Developer reports a NEW bug → ticket-creation HITL  *(role: DEVELOPER)*
- **Ask (realistic):** `The PDF invoice download is failing with a timeout — it's broken.`
  (or: `Users can't download their monthly report — the export just times out.`)
- **Why this triggers it (the actual logic):** the cross_source agent suggests a ticket only when
  **(a)** the query contains an unresolved-problem signal (`error/bug/broken/failing/timeout/500/
  can't/…`), **(b)** it is NOT a historical "how was X fixed" question, AND **(c)** a secondary
  LLM check confirms **no existing Jira ticket** already covers it. PDF-invoice/report-export is
  not in SDLC-1…10, so it returns `should_create=True`.
- **Expected output:** an investigation answer **+ a "Create ticket" HITL card** (title, description,
  priority, labels) → Approve creates it in Jira, Reject cancels.
- **Why your earlier queries did NOT trigger it (correct behavior):**
  - "SDLC-5 has a bug" → SDLC-5 already exists → suggestion skipped (no duplicate).
  - "Is there a Slack issue with no ticket?" → surfaces the CORS issue, but **SDLC-3 already
    covers CORS** → skipped. (Also it's a meta-question, not how a real dev phrases a bug.)
- **Proves:** agent initiative + HITL + duplicate-guard (won't spam tickets). 
- **Note:** verify the exact trigger phrase on the **70B** model — the 8B sometimes mis-maps a
  novel bug onto a loosely-related ticket and skips creation.
- [ ] Verified (Approve creates)   [ ] Verified (Reject cancels)   [ ] Verified duplicate-skip on "SDLC-5 has a bug"

## Scenario 10 — Live data preferred over stale local chunk  *(any role, needs live Jira)*
- **Setup:** a ticket whose status is OLD in an ingested chunk but UPDATED in live Jira.
- **Ask:** `What's the current status of SDLC-5?`
- **Expected output:** Answer reflects the **live Jira** status, not the stale ingested doc.
- **How it works (be honest):** for "current"-intent queries the agent puts **live MCP data
  before RAG chunks** in the prompt and instructs the LLM to prefer live
  (`cross_source_agent.py` temporal-intent ordering). It's prompt-ordering + an intent note,
  **not** a hard dedup — say it that way if asked.
- **Proves:** Tool-vs-knowledge precedence, freshness handling.
- [ ] Verified

## Scenario 11 — Memory in action (multi-turn + episodic)  *(any role)*
Memory is invisible on single questions — you must show it with a **conversation**.
All four layers are live (verified: 21 semantic facts, 1 episodic event stored).

- **11a — Session / conversational memory (follow-ups):** ask in sequence, same session:
  1. `What's blocking the dashboard feature?`
  2. `Who is working on it?`  ← "it" only resolves because prior turns are loaded
  3. `What was the root cause?`  ← continues the thread without re-stating "dashboard"
  → Proves: `retrieve_memory` loads recent turns from SQLite; >6 turns get LLM-summarized.
- **11b — Episodic memory (action timeline):** after you **Approve** a ticket-creation or
  reviewer-assignment (Scenario 3/9), ask: `What actions have been taken recently?`
  → Proves: approved HITL actions are recorded (`episodic_memory.record_event`) and recalled
  for historical queries (`search_events`).
- **11c — Semantic cache:** ask the **same** question twice → the second reply shows the
  **⚡ cached** chip (served from Redis, no LLM call).
- **Proves:** real memory architecture, not a stateless chatbot. **High** for Architecture.
- [ ] Verified follow-up   [ ] Verified episodic timeline   [ ] Verified cached chip

---

## Realistic query bank per role (pick from these in the demo)

**Developer**
- `SDLC-5 has a bug, what could be the fix?` → ticket lookup + solutions
- `The PDF invoice download is failing with a timeout.` → **ticket-creation HITL**
- `Review the open PRs and assign a reviewer.` / `Approve PR-49.` → PR HITL
- `What caused the CORS outage and how was it fixed?` → historical (no ticket suggested)

**Manager**
- `Are we at risk of missing the sprint? Why?` → risk + root cause
- `What's blocking us right now?` → cross-source blockers
- `Are we ready to release v2.1?` → release readiness (GO/NO-GO + HITL)
- `Notify #engineering-manager about the sprint status.` → notify

**Stakeholder**
- `What's the status of the dashboard feature?` → business-language status
- `When will the payment integration be ready?` → blocker-aware status
- `Give me a high-level summary of this sprint.` → cross-source synthesis

---

## What is LIVE vs MOCK in the demo (be upfront about this)

| MCP tool | Mode | Why |
|---|---|---|
| Jira | **Live** if `.env` creds set, else auto-mock | real connector, `is_available()` decides |
| GitHub | **Live** if `GITHUB_TOKEN` set, else auto-mock | real connector |
| Confluence | **Live** if creds set, else auto-mock | real connector |
| Slack | **Mock** (`use_mock: true`) | no real workspace history to search; deterministic demo |
| Teams | **Mock** (`use_mock: true`) | real Graph connector built, no Azure app for demo |
| Drive | **Mock** (`use_mock: true`) | real connector built, mock files for demo |

One-liner for graders: *"Every connector is real API-client code; Slack/Teams/Drive run in
mock mode so the demo is offline and deterministic. Flip `use_mock: false` + add creds = live."*

---

## Evaluation layer (where quality is measured)

`backend/core/metrics.py`, run live (fire-and-forget) from `nodes.py:547` after every answer,
results in `data/eval_results.jsonl`, viewable in admin page `04_evaluation`.

| Metric | Implemented | Where |
|---|---|---|
| Faithfulness (LLM judge, grounded?) | ✅ live + batch | `metrics.py` `faithfulness_score` |
| Answer relevancy (cosine) | ✅ live + batch | `metrics.py` `answer_relevancy` |
| Context precision (RAGAS) | ✅ batch | `metrics.py` `context_precision` |
| Context recall (RAGAS) | ✅ batch | `metrics.py` `context_recall` |
| Answer correctness (RAGAS) | ✅ batch | `metrics.py` `answer_correctness` |
| Intent accuracy | ✅ batch | `intent_accuracy` |

**Live** (every chat): faithfulness + relevancy, flagged if faithfulness < 0.45.
**Batch** (needs ground truth from `data/eval_set.json`): precision, recall, correctness too —
run from admin page `04_evaluation` ("Run evaluation"). The composite now blends all five.

### How to demo the eval layer
1. Run a few chat queries → open admin `04_evaluation` → show the live faithfulness/relevancy
   per query (and any low-faithfulness flag).
2. Click **Run evaluation** (batch over `eval_set.json`) → show the full RAGAS table:
   precision / recall / faithfulness / relevancy / correctness / composite per query.
- **Suggested batch-eval query already in the set:** `What is blocking the dashboard feature?`
  (expected_keywords nginx/CORS/SDLC-1038/Alice/dashboard → recall & precision both score;
  expected_response_keywords blocked/nginx/dashboard/CORS → correctness scores).

---

## Memory architecture (all four layers are LIVE — verified)

| Layer | Store | Written | Read | Demo it with |
|---|---|---|---|---|
| Conversational / session | SQLite | every turn (`chat.py` `asave_turn`) | `retrieve_memory` loads last 5 turns; >6 summarized | Scenario 11a follow-ups |
| Semantic (long-term facts) | Qdrant `semantic_memory` (21 pts) | after each answer (`extract_and_save`) | `retrieve_facts` per query | facts resurface across sessions |
| Episodic (action timeline) | Qdrant `episodic_memory` (1 pt) | on HITL **approve** (`record_event`) | `search_events` for historical queries | Scenario 11b |
| Semantic cache | Redis | after each answer (`set_cached`) | `cache_check` node (`get_cached`) | Scenario 11c (⚡ cached chip) |

Talking point: *"This is not a stateless chatbot — it remembers the conversation (session),
learns durable facts (semantic), records what humans approved (episodic), and caches answers
(Redis). All four are wired into the live graph, not stubs."*
