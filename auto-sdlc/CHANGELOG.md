# Daily autonomous engineering run — changelog

## 2026-09-27

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
