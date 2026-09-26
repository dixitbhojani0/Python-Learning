# Daily autonomous engineering run — ai-sdlc-assistant + sdlc-mcp-server

You are the daily engineer on this repo. `run-daily.ps1` has already checked out a fresh
branch `auto/<today>` from an up-to-date `master`. You have roughly one working day of scope.
When you exit, the runner re-runs the full test gate, scans for secrets, pushes the branch,
and merges it into `master` only if everything is green.

## Scope — hard limits
- Edit only inside `ai-sdlc-assistant/`, `sdlc-mcp-server/`, and `auto-sdlc/`. Nothing else in the repo.
- Follow `ai-sdlc-assistant/CLAUDE.md` and the standards files it points to. Read the relevant one before touching an area.
- **Never lose an existing feature.** Never delete, skip, xfail, or weaken an existing test to make the gate pass.
  If a refactor is needed, the old behaviour must stay covered by tests.
- No secrets in code, config or commits. Don't read `.env` files; use `.env.example` for names.
- Git: commit on the current branch only. Do NOT push, merge, rebase, reset, switch branches, or change git config — the runner owns all of that.

## Steps
1. **Orient.** Read the last ~5 entries of `auto-sdlc/CHANGELOG.md` (if it exists) and the open items (☐ / ◐)
   in `ai-sdlc-assistant/REDESIGN_BUGS.md`, which is the backlog.
2. **Refill the backlog if thin.** If fewer than 5 items are open, do a research pass and append new items to section D
   of REDESIGN_BUGS.md (same format: id, severity, problem, acceptance criteria):
   - **SDLC coverage.** Map the lifecycle (plan → requirements → design → build → review → test → release → operate → feedback)
     against what the assistant can answer or do today, and list the gaps.
   - **MCP gaps.** Compare the tools in `sdlc-mcp-server/tools/` to what those phases need
     (e.g. Jira transitions and sprints, GitHub CI and checks status, Confluence write, release notes).
   - **Enterprise chat UI.** Use the `impeccable` and `ui-ux-pro-max` skills plus WebSearch for current product-grade patterns:
     streaming states, citations/sources panel, tool-call and decision trace, HITL approval cards, conversation history
     and search, keyboard shortcuts, empty/error/loading states, accessibility (WCAG AA), dark mode, responsive layout.
3. **Pick 1–3 items** that fit one day. Priority: failing tests / broken full flow > 🔴 bugs > end-to-end flow gaps >
   MCP tools > 🟠 enhancements > UI polish.
4. **Implement each item with tests.** Backend: pytest in `ai-sdlc-assistant/tests/unit`. MCP: `sdlc-mcp-server/tests`.
   UI: Angular spec next to the component. For UI changes, also run `impeccable` critique/polish on the changed screen.
5. **Run the gate yourself before each commit.** Every command must pass:
   - `ai-sdlc-assistant/.venv/Scripts/python.exe -m pytest ai-sdlc-assistant/tests/unit -q -p no:cacheprovider`
   - `cd sdlc-mcp-server && ../ai-sdlc-assistant/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
   - `cd ai-sdlc-assistant/frontend-angular && npx ng test --watch=false && npx ng build`
6. **Commit** each item separately, e.g. `fix(hitl): B2 — single ticket-creation path`. Mark the item ☑ in REDESIGN_BUGS.md in the same commit.
7. **Log it.** Append today's entry to `auto-sdlc/CHANGELOG.md` (date, items done, tests added, anything left half-done
   and why) and commit it last.

If something is blocked (needs a credential, a paid API, or a product decision), write it in the CHANGELOG under
"Needs Dixit" and move on to the next item. Don't stop the run for it.

Finish by printing a 5-line summary: items done, commits made, gate status, open follow-ups.
