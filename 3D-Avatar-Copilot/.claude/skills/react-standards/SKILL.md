---
name: react-standards
description: React 19 + TypeScript coding standards for frontend/ — naming, structure, hooks, security, tooling. Use when writing, reviewing, or refactoring any code under frontend/src.
---

# React + TypeScript Standards (frontend/)

## Naming conventions (industry standard)

| Thing | Convention | Example in this repo |
|---|---|---|
| Component (function + file) | PascalCase | `ConversationPanel.tsx`, `AdminPanel` |
| Hooks | `use` prefix, camelCase | `useAudioLevels` |
| Variables, functions, props | camelCase | `personaName`, `onMicClick` |
| Module-level constants | UPPER_SNAKE_CASE | `BAR_COUNT`, `BLANK` |
| Types / interfaces | PascalCase, no `I` prefix | `Scene`, `ChatMessage`, `AvatarProvider` |
| Non-component modules | camelCase or kebab | `client.ts`, `script-data` |
| Event props | `on<Event>`; handlers `handle<Event>` | `onScenePlayed` |
| Booleans | is/has/can/show prefix | `hasVideo`, `canReconnect`, `showCards` |

## Structure rules (this project's layout is the pattern)

- One component per file; component files contain JSX only — logic lives in plain TS modules
  (`engine/SceneEngine.ts`, `providers/*.ts` are framework-free classes; keep it that way).
- Shared types in `src/types.ts` — never redeclare a shape locally that exists there.
- All backend calls go through `src/api/client.ts` — no `fetch` anywhere else.
- URL/param/flag parsing only in `src/config.ts` — components read `config`, never `location`.
- Prefer plain state + props; introduce context only when prop drilling exceeds ~3 levels;
  no Redux/state library until real cross-page state exists.

## TypeScript rules

- `strict` stays on (tsconfig already enforces it); never `any` — use `unknown` + narrowing.
- `import type { X }` for type-only imports (keeps erasableSyntaxOnly happy).
- No constructor parameter properties, no enums — TS6 `erasableSyntaxOnly` forbids them;
  use explicit fields and union string literals (`type Status = "idle" | ...`).
- Exported functions and public class members get explicit return types.

## Hooks discipline

- Every `useEffect` returns its cleanup when it subscribes/allocates (listeners, AudioContext,
  timers — see `LiveWaveform.tsx` for the pattern).
- Dependencies arrays are complete; wrap callbacks passed down in `useCallback` only when a
  child effect depends on identity, not by default.
- No state updates in render; derive values instead of mirroring props into state.

## Security (OWASP-aligned, frontend)

- **Never `dangerouslySetInnerHTML`.** Backend sends structured JSON (cards), React renders it —
  this is the XSS boundary; keep it.
- No secrets in frontend code or `VITE_*` env vars — everything prefixed `VITE_` ships to the
  browser. API keys live in `backend/.env` only.
- External links: `rel="noopener noreferrer"` with `target="_blank"`.
- Validate/parse all API responses at the boundary (`api/client.ts` throws on non-OK).
- `npm audit` before releases; pin dependency majors in package.json.

## Tooling

- Linter: the Vite scaffold's config (oxlint/eslint) must pass before commit: `npm run lint`.
- Build must be clean: `npm run build` (tsc + vite) — type errors are build failures, never ignored.
- Formatting: 2-space indent, double quotes, trailing commas (match existing files).

## Review checklist

1. Names follow the table above; no `any`; no missing effect cleanup.
2. New backend calls added to `api/client.ts` only; new flags to `config.ts` only.
3. No raw HTML injection; no secrets; `npm run build` passes.
