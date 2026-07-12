# AI SDLC Assistant — Implementation Flow (standards-first, ground-up)

> A step-by-step build plan derived from **industry standards**, not from "what we already have".
> Each decision states **what / why (standard) / tool / how we verify**. If a standard was unknown it
> was researched and cited. Companion docs: `REDESIGN_BUGS.md` (what's wrong) and `TEST_SUITE.md`
> (how to verify). This file = **what to build and in what order**, even if rebuilding from scratch.

---

## 0. Guiding principles (non-negotiable, applied to every step)

1. **Standards-first, not boundary-first.** We pick the right approach and justify it; we don't keep a
   weak design just because it exists. Refactor where the skeleton is right (LangGraph/FastAPI/Qdrant),
   rebuild where it isn't (MCP, memory, chunking, cache).
2. **Probabilistic, not hardcoded (P1).** Routing, recall-intent, cacheability, duplicate/in-domain,
   tool choice → decided by LLM + embeddings + reranker confidence, not regex/keyword lists.
3. **Defense-in-depth safety (OWASP LLM Top 10).** Input guardrail → model → output guardrail →
   authorization at the **tool-execution** layer → HITL for sensitive actions. No single layer trusted.
4. **Every component is testable + observable.** A DeepEval assertion + a trace for each behavior, so
   "run once" replaces manual type-wait-check.
5. **Free-tier/local where possible, production-pattern always.** Groq, local embeddings, Docker — but
   the architecture mirrors production.

**Target stack** (verdict from `REDESIGN_BUGS.md` §G):
```
FastAPI host ── LangGraph supervisor (LLM-first)
  ├─ SAFEGUARDS: input+output moderation (Llama Guard via Groq / LLM Guard) + injection + faithfulness
  ├─ MCP CLIENT (langchain-mcp-adapters) → tools/list → LLM tool-use loop
  │     └─ official Atlassian/GitHub MCP servers (+ our FastMCP server for custom tools)
  ├─ RAG: recursive-512 parent-child + hybrid(BM25+vector+RRF) + cross-encoder rerank + corrective
  ├─ MEMORY: Redis(working) + Qdrant/Mem0(semantic) + episodic + summary — INJECTED for all agents
  ├─ HITL: propose→commit, role gates @ execution, scoped approvals, audit trail
  ├─ EVAL: DeepEval CI gate (faithfulness/precision/recall/relevancy/tool-use)
  └─ OBSERVABILITY: LangSmith/Langfuse traces + per-response trace panel
```

---

## Phase 0 — Foundations & contracts  *(keep — already standard)*
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 0.1 | Config-as-source-of-truth (YAML, hot-reload) | no hardcoded strings; 12-factor | config loads, hot-reload |
| 0.2 | Provider abstraction (`BaseLLMProvider` + factory) | swap models w/o code change | Groq + 1 fallback registered |
| 0.3 | LangGraph state + graph skeleton | durable state + HITL checkpoints (LangGraph is the 2026 pick for this) | graph compiles |
| 0.4 | FastAPI boundary + demo token auth + **roles** | thin client; role context drives authz | 3 roles resolve |

## Phase 1 — Safeguards layer  *(build EARLY, not last)*
| Step | What | Why (standard) | Tool | Verify |
|---|---|---|---|---|
| 1.1 | **Input guardrail** before the model: prompt-injection + **harmful/toxic content** + PII + banned-topics | "inspect inputs before the model sees them"; OWASP LLM01/02; harmful-content moderation is mandatory | **Llama Guard via Groq** (free, same provider) and/or **LLM Guard** scanners; keep our injection block | SEC-1, harmful-content cases → 403 |
| 1.2 | **Output guardrail** before the user: moderation + faithfulness/grounding check | "validate outputs before the user sees them" | LLM Guard output scanners + our faithfulness judge | EV-2 flags ungrounded; toxic output blocked |
| 1.3 | Authicztion at **tool-execution** layer (role→action gate) | "enforce authz at tool execution, not output" | gate in HITL/execute | SEC-4/5, role-CRUD denials |
| 1.4 | Rate limit + audit log of every action | abuse + traceability | slowapi + episodic log | SEC-8 → 429; audit entries |

## Phase 2 — RAG, done to standard
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 2.1 | Ingestion: multi-format loaders (PDF/docx/MD/images+OCR) | knowledge base must accept all doc types | docs ingest, images OCR'd |
| 2.2 | **Recursive ~512-token parent-child** chunking (replace fragmented chunks) | recursive-512 benches 69–90% recall; parent-child = small-to-find/large-to-answer | RG chunks coherent, no 40-token fragments (fixes B8) |
| 2.3 | Contextual prefix per chunk before embedding | embeds doc context, not just fragment | retrieval improves on ambiguous queries |
| 2.4 | Embeddings: all-MiniLM now → **BGE-M3** upgrade path | BGE-M3 = dense+sparse+multivector, 8k, open | recall benchmark |
| 2.5 | Retrieval: hybrid **BM25+vector+RRF** + **cross-encoder rerank** + confidence tiers | hybrid+rerank is the standard; tiers gate behavior | RG-1..3 |
| 2.6 | Corrective RAG (1 retry) + **probabilistic recall-intent** (no keyword regex) | CRAG/Self-RAG; P1 | RG-4/5/8, recall picks right doc (fixes B8) |
| 2.7 | RAG eval wired | faithfulness/context-precision/recall are the RAGAS core | EV-4 batch passes thresholds |

## Phase 3 — MCP (real protocol, replace REST adapter)
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 3.1 | Add **MCP client** (`langchain-mcp-adapters` `MultiServerMCPClient`) | MCP is the 2026 tool standard; transfers across all stacks | `tools/list` returns tools |
| 3.2 | Connect each connector to a real MCP server (table below); **wrap any gap in our own FastMCP server** | use vendor servers where they exist; one consistent protocol for the rest | live tool calls per connector |
| 3.3 | **LLM tool-use loop** — supervisor selects **and chains/fans-out** tools from descriptions; can emit **multiple tool calls in one turn** | the LLM decides+composes tools (modern function-calling supports parallel tool calls); P1, B7/B9 | XC-* + MT-* multi-tool queries |
| 3.4 | Parallel execution: **fan-out** (e.g. review N PRs) and **independent multi-tool** (e.g. Jira + Slack) run concurrently under concurrency cap, per-call failure isolation | efficiency + resilience (we already do `gather`+semaphore) | parallel timing; 4-PR fan-out; one failure ≠ crash |
| 3.5 | Creds via OAuth/env; HITL gate at execution | least privilege | no hardcoded secrets |

**Connector → MCP server coverage (verified 2026-06):**
| Connector | Real MCP server available? | Plan |
|---|---|---|
| **Jira** | ✅ Atlassian official Remote/Rovo MCP server (`mcp.atlassian.com`) | use official (OAuth) |
| **Confluence** | ✅ same Atlassian server | use official |
| **GitHub** | ✅ GitHub official MCP server (hosted or self-host) | use official |
| **Slack** | ✅ official Slack MCP server (+ community, Zencoder-maintained) | use official |
| **Google Drive** | ✅ official MCP server (file access + search) | use official |
| **Microsoft Teams** | ❌ **no official MCP server yet** (mid-2026; hinted, not shipped) | **wrap our Graph connector in our own FastMCP server** |
| Custom/internal (e.g. monitoring, our mocks) | n/a | our own FastMCP server |

> Verdict: **every connector we need is covered** — 5 via official/community servers, Teams (+ any custom
> tool) via our own FastMCP server. The FastMCP fallback is the completeness guarantee: anything without a
> vendor server is exposed over the *same* MCP protocol, so the client/agent code is uniform.
>
> **If more are needed later** (scope-dependent, not required now): a **monitoring/alerting** server (for
> the "12% of traffic 500s" trigger), **CI/CD** (GitHub Actions is already covered by the GitHub server;
> Jenkins/GitLab would need their own), **calendar/email**. Add by pointing the client at another server —
> zero agent-code change (the discovery payoff).

### Phase 3 — Build status (updated 2026-06-28)  *(detailed log: `REDESIGN_BUGS.md` B7)*
| Step | Status | Notes |
|---|---|---|
| 3.1 MCP client (`MultiServerMCPClient`) | ✅ done | `backend/mcp_client/` — `tools/list` verified, 16 read + 6 write tools discovered |
| 3.2 Connector → MCP server | ✅ done **(deviation — deliberate)** | Built **our own FastMCP server** (`backend/mcp_server/`) wrapping ALL connectors, **instead of** official Atlassian/GitHub servers. Why: offline/free demo, reuse connectors+mocks, no OAuth, and it demonstrates building **server _and_ client** (stronger for MCP-graded). Official servers = optional Step-6 stretch (interop). |
| 3.3 LLM tool-use loop (select + chain) | ✅ done | `gather_via_tools` — verified chaining jira+github, jira+slack from descriptions |
| 3.4 Parallel fan-out / multi-tool | ✅ done | a turn's tool calls run via `asyncio.gather` under `Semaphore(4)`, per-call failure isolation (B7e) |
| 3.5 Creds via env; HITL gate at execution | ✅ done | env creds ✅; read-only autonomous loop ✅; **all HITL writes execute over MCP** (`_mcp_write`→`call_mcp_tool`, Step 4d) — role gates kept; B2 fabricated-id removed |

**Also done beyond the original Phase-3 scope (production hardening):** provider-agnostic tool-use seam
(`BaseLLMProvider.get_chat_model` — swap provider, zero MCP change); **gather-then-synthesize** agent
(`MCPAgent`) wired as the default generalist; **read vs write tool split** (autonomous loop is read-only,
writes only via approved HITL); MCP server dockerized as its own compose service.

**Remaining in the MCP phase:** MCPAgent write-intent→proposal (restores duplicate-ticket suggestion) ·
deferred hardening **B7a** silent-mock-fallback, **B7b** server auth, **B7c** fail-loud when live tools
down. Official-server interop = optional. *(4d HITL-execute-over-MCP ✅ · all 5 specialist agents migrated
to `call_mcp_tool` ✅ — only the legacy cross_source shim remains, unrouted.)*

**Engineering-hardening — ✅ done (`REDESIGN_BUGS.md` B7d/e/f):**
**B7d** schemas cached + client singleton (per-call stateless session by design) + `clear_tools_cache()` ·
**B7e** `asyncio.gather` parallel tool calls, capped + isolated (= 3.4) ·
**B7f** `_normalize_result` JSON-string/list normalization, verified.

## Phase 4 — Agentic orchestration
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 4.1 | LangGraph supervisor, **LLM-first routing** (keyword only as LLM-down fallback) | classifier/supervisor routing; P1 | routing tests, A1–A6 |
| 4.2 | Specialist agents OR ReAct tool-use loop; **one canonical action path** per action (e.g. ticket-create) | supervisor-worker; remove dual paths (B2/B5) | TK-3 yields real Jira number |
| 4.3 | Multi-step reasoning; A2A only if agent↔agent needed | A2A = agent protocol (vs MCP=tool) | complex query trajectory |

## Phase 5 — Memory (real, injected)
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 5.1 | **Inject** semantic facts + summary into prompts for **all** agents (fix dead path) | extract→consolidate→store→**inject**; ours is half-dead (B10) | MEM-4/5/6 pass |
| 5.2 | Layers: Redis(working) + Qdrant(semantic) + episodic + summary; consider **Mem0/LangMem** | standard memory split | facts reused across sessions |
| 5.3 | **Remove/scope Redis response cache** (RAG-only, never live MCP) | cache wrong for live data (B6) | live update reflected immediately (fixes B1) |

## Phase 6 — HITL (human-in-the-loop)
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 6.1 | propose→commit on durable store (Redis) | the reliable HITL pattern | approve/reject lifecycle |
| 6.2 | **Scoped approvals**: Approve / Reject for risky; +Approve-all-this-conversation for low-risk notify | risk-tiered approval (E8) | NT-2, ticket/PR = approve/reject only |
| 6.3 | Audit trail of approvals (episodic) | accountability | MEM-2 timeline |
| 6.4 | **Multi-action HITL**: when one turn proposes several risky writes (e.g. *create ticket + notify Slack*, or assign reviewers to *4 PRs*), present them as a **batched approval** (approve all / per-item / reject); reads run in parallel first, writes commit only after approval | a turn can need >1 action; each risky write still needs human sign-off (propose→commit per action) | MT-1/MT-2 multi-action queries |

## Phase 7 — Roles, persona & capabilities
| Step | What | Why | Verify |
|---|---|---|---|
| 7.1 | Role capability matrix: dev full CRUD; manager oversight; stakeholder read+create→notify | clear role-based assistant (the product goal; E1–E7) | TK-12/13, PR-6 role gates |
| 7.2 | Persona adaptation AFTER agents; anti-fabrication; surface ticket **comments/effort** | data vs presentation; honest comms (B3) | PER-1, TK-1 shows comments |

## Phase 8 — Eval, observability, testing  *(continuous, wired from day 1)*
| Step | What | Why (standard) | Verify |
|---|---|---|---|
| 8.1 | **DeepEval** test suite from `TEST_SUITE.md` + `eval_set.json`; CI gate on every PR | "Pytest for LLMs"; 52% eval gap kills quality; eliminates manual testing | `deepeval test run` green; fails on regression |
| 8.2 | Tracing: **LangSmith** (hooks exist) or **Langfuse** (self-host) | observability standard; agent trajectory | traces show tools+chunks+routing reason |
| 8.3 | Per-response **trace panel** in UI ("why this answer") | explainability (E9) | trace visible in chat |

## Phase 9 — Frontend & demo
| Step | What | Why | Verify |
|---|---|---|---|
| 9.1 | **Angular only** — single app for **both chat + admin** (no Chainlit/other frontend) | one thin client; UI graded Low, don't over-invest or split stacks | chips + trace render in Angular |
| 9.2 | Demo script across roles + scenarios | live demonstration (graded High) | `DEMO_PLAN.md` scenarios pass |

---

## Coverage matrix — every `REDESIGN_BUGS.md` item → where it's fixed here
Proof that nothing is dropped. Every bug (B), enhancement (E), principle (P), and open question (C)
maps to a phase. Testing is its own pillar (Phase 8 + `TEST_SUITE.md`).
| Tracker item | Fixed in | Note |
|---|---|---|
| **B1** stale risk/blocker after Jira update | Phase **5.3** (+5.1) | remove live-data cache → fresh reads |
| **B2** two ticket-create paths / wrong number | Phase **4.2** + **3** | one canonical path via real Jira MCP |
| **B3** ticket comments not surfaced | Phase **7.2** | render latest dev comment + effort |
| **B4** over-eager match + verbose suggestion | Phase **2.6** + **4.2** | probabilistic dup-check; trim proposal |
| **B5** routing inconsistency (ticket create) | Phase **4.2** | single action path |
| **B6** Redis cache wrong for live data | Phase **5.3** | remove / scope to RAG-only |
| **B7** not real MCP | Phase **3** | MCP client + official/FastMCP servers |
| **B8** RAG noisy / wrong doc / chunking | Phase **2.2 + 2.6** | recursive-512; probabilistic recall |
| **B9** static tool selection / no chaining | Phase **3.3** | LLM tool-use loop |
| **B10** memory half-dead (not injected) | Phase **5.1 + 5.2** | inject for all agents; Mem0/LangMem |
| **E1** role→action permission model | Phase **1.3 + 7.1** | authz at execution layer |
| **E2** stakeholder create → Slack notify | Phase **7.1** | role matrix + notify |
| **E3** status returns full detail | Phase **7.2** | status incl. comment/assignee/ETA |
| **E4** comments = source of truth | Phase **7.2** | effort/size from dev comments |
| **E5** full PR lifecycle (incl. comment CRUD) | Phase **3.2 + 7.1** | PR tools via GitHub MCP + gates |
| **E6** Jira comment CRUD | Phase **3.2 + 7.1** | add/edit/delete comment tools |
| **E7** edit/delete ticket + deassign | Phase **3.2 + 7.1** | tools + role gate |
| **E8** HITL scoped approvals | Phase **6.2** | approve / approve-all / reject |
| **E9** frontend trace panel | Phase **8.3** | "why this answer" |
| **E10** automated suite + CI gate | Phase **8.1** | DeepEval `deepeval test run` |
| **E11** observability + runner | Phase **8.1 + 8.2** | trace + assertions, no manual |
| **P1** no hardcoding / probabilistic | **Principle 2** (cross-cutting) | applied in 2.6, 4.1, 5.3 |
| **C6** keep-adapter vs real-MCP decision | **resolved → Phase 3** | go real MCP |
| **NEW** harmful-content / prompt safeguards | Phase **1** | input+output moderation (Llama Guard/LLM Guard) |
| **NEW** standards-based testing | Phase **8** + `TEST_SUITE.md` | DeepEval + tracing, CI regression |

## Decision log (tool ↔ why ↔ standard)
| Area | Choice | Why / standard |
|---|---|---|
| Orchestration | LangGraph | top framework for stateful + HITL workflows |
| MCP | langchain-mcp-adapters + official servers + FastMCP | MCP = 2026 tool standard; LLM-driven tool use |
| Chunking | recursive-512 parent-child | best recall in 2026 benchmarks |
| Embeddings | all-MiniLM → BGE-M3 | open, multi-vector, long-context upgrade |
| Retrieval | hybrid + cross-encoder rerank + CRAG | standard agentic-RAG pattern |
| Memory | Redis + Qdrant + Mem0/LangMem, injected | extract→consolidate→store→inject |
| Safeguards | Llama Guard (Groq) / LLM Guard + injection + faithfulness | input+output moderation, defense-in-depth, OWASP |
| HITL | propose→commit + scoped approvals | reliable HITL pattern |
| Eval | DeepEval (+Ragas optional) | pytest-native CI gate |
| Observability | LangSmith / Langfuse | tracing standard |

## Migration reality (not a blind rewrite)
- **Keep & refactor:** Phase 0, 4 (routing already LLM-first), 6 (HITL), 7 (persona), 9 (UI).
- **Rebuild structurally:** Phase 1 (safeguards = mostly new), 2.2/2.6 (chunking + probabilistic recall),
  3 (real MCP), 5 (memory injection + cache removal).
- **Add:** Phase 8 (DeepEval + tracing).
- Order to ship value fast: **1 → 2 → 5 → 3 → 4 → 6/7 → 8 → 9** (safety first, then correctness, then
  the headline MCP/agentic upgrades, then prove it with eval).

Sources: [AI Agents Stack 2026 — O'Reilly](https://www.oreilly.com/radar/the-ai-agents-stack-2026-edition/) ·
[Chunking strategies 2026 — Firecrawl](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) ·
[langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) ·
[Mem0 (arXiv)](https://arxiv.org/pdf/2504.19413) ·
[LLM safety & guardrails 2026](https://pdpspectra.com/blog/llm-safety-guardrails-2026/) ·
[OWASP Top 10 for LLM Apps](https://owasp.org/www-project-top-10-for-large-language-model-applications/) ·
[DeepEval — pytest LLM evals](https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd)
---

## Addendum — items found in a full review (APPEND-ONLY, 2026-06-27)
> Nothing above was removed. These are gaps not previously scheduled in any plan doc, surfaced by a
> complete sweep of the conversation + assignment deliverables. Added as new phases 10–12 + notes.

### Phase 10 — Proactive / event-driven (not just request-response)
| Step | What | Why (standard / assignment) | Verify |
|---|---|---|---|
| 10.1 | **GitHub webhook → auto PR review** (agent triggered on PR open, posts result to Slack) | assignment "agent-assisted task coordination"; agents shouldn't only react to chat | webhook fires → review posted |
| 10.2 | **Scheduler** (e.g. daily standup / risk digest to a channel) | proactive notifications; "stakeholder notifications" capability | cron fires → message sent |
| 10.3 | Event/trigger routing through the same safeguards + HITL | a webhook-triggered write still needs guardrails/approval | triggered action gated |

### Phase 11 — Operations, resilience & cost
| Step | What | Why | Verify |
|---|---|---|---|
| 11.1 | **Dockerize new services** (our FastMCP server, Langfuse/observability, any worker) + compose | reproducible deploy; everything-in-Docker | `docker compose up` brings full stack |
| 11.2 | **LLM quota/cost resilience**: real cross-provider fallback (e.g. Groq→Gemini), backoff, semantic-degrade | Groq TPD limit is the #1 live-demo risk; one provider = single point of failure | fallback engages on 429; demo survives |
| 11.3 | Cost/latency budget: cap synchronous LLM judge calls; batch/async where safe | sync faithfulness adds a call per answer — watch quota | latency + token budget tracked |

### Phase 12 — Deliverables (assignment-graded)
| Step | What | Why | Verify |
|---|---|---|---|
| 12.1 | **Update the R&D / Solution Document** to match this standards-first design (problem, scope, architecture, agent workflow, RAG strategy, MCP usage, toolkit, assumptions, challenges) | required deliverable; current v3 has drifted from reality | doc matches running system |
| 12.2 | **Low-Level Architecture diagram** (components, data flow, agent responsibilities, HITL points) | required for final demonstration | diagram reviewed |
| 12.3 | Demo rehearsal across all roles + scenarios (`DEMO_PLAN.md`) | "Working Demonstration" graded High | dry-run passes |

### Note added to Phase 5 (memory) — procedural memory
- Memory standards list **three** long-term scopes: semantic + episodic + **procedural** (learned
  rules/instructions the agent updates itself). We cover semantic + episodic; **procedural** is a
  future option (e.g. learned routing/permission preferences) — add if scope grows. (LangMem supports it.)

### Coverage delta (these IDs now also tracked)
Proactive triggers (10), Ops/quota/Docker (11), Solution-doc + LLA diagram + demo (12), procedural
memory (5 note). Combined with the earlier Coverage matrix, **all B/E/P/C items + assignment
deliverables are now scheduled.**

### Standards backing for the addendum (why + how + source) — APPEND-ONLY
| Item | Industry standard | Why (the principle) | How (the pattern) | Source |
|---|---|---|---|---|
| **10.1/10.2 Webhook + scheduler (event-driven/proactive)** | Event-Driven Agent Architecture; proactive agents | agents should be *event consumers*, dormant until a relevant event — not chat-only; "shift from reactive to anticipatory" | Event producer → event bus/HTTP webhook → agent execution; scheduled background agents for digests | AWS event-driven agentic guidance; Confluent; MindStudio proactive agents |
| **10.3 triggered actions still gated** | same defense-in-depth | a webhook-initiated write is still a write | route triggers through the Phase-1 guardrails + Phase-6 HITL | OWASP LLM defense-in-depth |
| **11.2 Multi-provider LLM fallback** | **LLM Gateway / Router pattern (LiteLLM)** | one provider = single point of failure; quota/429 must not break the app | router with ordered fallbacks, retries (exp. backoff), cooldowns, error-normalization (RateLimitError→fallback) | LiteLLM Router/Reliability docs |
| **11.1 Dockerize + compose** | 12-factor / containerization | reproducible, isolated, scalable deploy; new services (FastMCP, Langfuse) need their own containers | one service per container, config via env, `docker compose` for the stack | 12-factor app |
| **12.1 Solution document** | **arc42** architecture documentation template | a sustainable, standard structure for the R&D/solution deliverable (context, constraints, building blocks, runtime, deployment, cross-cutting, decisions) | fill the arc42 sections against the running system | arc42 template |
| **12.2 Low-Level Architecture diagram** | **C4 model** | a recognized way to show architecture at 4 levels (context→container→component→code) — exactly what the demo asks for | draw Container + Component diagrams of the agent system | C4 model |
| **Phase-5 procedural memory** | **CoALA** (Cognitive Architectures for Language Agents) | CoALA defines long-term memory as semantic + episodic + **procedural** (skills/rules); we had only the first two | store learned rules/skills explicitly (code/config the agent can update), not just facts | CoALA (arXiv 2309.02427) |

Sources (addendum): [Event-driven agentic AI — AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-serverless/event-driven-architecture.html) ·
[Proactive AI agents — MindStudio](https://www.mindstudio.ai/blog/what-is-proactive-ai-agents-shifting-reactive-anticipatory) ·
[LiteLLM Router (fallbacks/retries)](https://docs.litellm.ai/docs/router_architecture) ·
[arc42 template](https://arc42.org/overview) · [C4 model](https://c4model.com/) ·
[CoALA — Cognitive Architectures for Language Agents (arXiv)](https://arxiv.org/pdf/2309.02427)

### Clarification — why webhooks/scheduler are NOT covered by the MCP client (APPEND-ONLY)
**MCP and webhooks solve opposite directions — they compose, neither replaces the other.**

| | Direction | What it does | In our plan |
|---|---|---|---|
| **MCP client (Phase 3)** | **Outbound** (agent → tool) | the agent *calls* GitHub/Jira/Slack tools (request-response) | Phase 3 |
| **Webhook (Phase 10.1)** | **Inbound** (GitHub → us) | GitHub *pushes* a "PR opened" event to our endpoint to **start** an agent | Phase 10 (separate) |

- **Verified standard:** MCP today is **request-response only** (stdio / Streamable HTTP); the spec has
  **no first-class webhook/inbound-event primitive** — a "Triggers & Events" Working Group is specifying
  it but it's *"on the horizon,"* not shipped. So **the GitHub webhook must be built/integrated by us**;
  the GitHub MCP server does **not** provide it. *(Source: MCP Triggers & Events charter; Hookdeck.)*
- **How they compose (the real flow):**
  `GitHub webhook → our HTTP ingress endpoint → trigger agent → agent uses GitHub MCP tools to review →
  posts result via Slack MCP → risky writes still go through HITL`. Event-in = webhook; act = MCP.
- **Pattern/standard:** Event-Driven Architecture — *event producers (webhook, scheduler) → ingress/bus →
  agent execution*. Webhook is the simplest trigger transport; Kafka/RabbitMQ/Pub-Sub for scale.

**Scheduler (Phase 10.2) — framework/standard:** also **not** MCP (time is the event source, not a tool).
Standard = *scheduled background agents*. Tooling options by scale: **APScheduler** (in-process, fine for
this app) → **cron** → **Celery beat** → cloud schedulers (**EventBridge / Cloud Scheduler**) for
distributed. Pick APScheduler now; it's a recognized Python scheduling lib and matches the proactive-agent
pattern. *(Source: proactive/scheduled-agent guidance.)*

### Duplication check — are the addendum gaps already covered by an earlier phase?
| Addendum item | Already covered elsewhere? | Verdict |
|---|---|---|
| 10.1 webhook / 10.2 scheduler | **No** — Phase 3 (MCP) is *outbound* only; inbound triggers are a different concern | **net-new, keep** |
| 10.3 gate triggered writes | reuses Phase 1 + Phase 6 (by design) | composition, not duplication |
| 11.1 Docker/deploy | not in Phase 0 (which is app contracts, not deployment) | **net-new, keep** |
| 11.2 provider fallback/router | **partial** — Phase 0.2 only *registers* a fallback model; 11.2 adds the **gateway/router resilience** (cooldowns, ordered fallbacks, error-normalization) | **extends 0.2, not a duplicate** |
| 12.1 solution doc / 12.2 diagram | not anywhere | **net-new, keep** |
| Phase-5 procedural memory note | extends Phase 5 (semantic+episodic) | **addition, not duplicate** |

Sources (clarification): [MCP Triggers & Events charter](https://modelcontextprotocol.io/community/triggers-events/charter) ·
[Reliable MCP servers: async events & webhooks — Hookdeck](https://hookdeck.com/blog/mcp-event-gateway) ·
[Event-driven agentic AI — AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-serverless/event-driven-architecture.html)
