# FinAdvisor AI — 3D Avatar Advisor Copilot

A financial-advisor meeting-prep demo: two-way **voice conversation** with a photorealistic
talking avatar (lip sync included). The advisor asks by voice; the copilot answers by voice
while a chat-style conversation panel logs the exchange, with info cards (client snapshot,
knowledge sources, compliance checklist, follow-up note) inline under the answers.
Ends on a 4-value closing screen. Deep-navy frosted-glass UI, sky-blue accent.

## Architecture

```
frontend/  React 19 + TypeScript + Vite          backend/  FastAPI (Python)
  src/api/client.ts   ← only place URLs live       app/routers/session.py  POST /api/v1/session
  src/providers/      ← AvatarProvider interface   app/routers/script.py   GET|PUT /api/v1/script
  src/engine/         ← framework-free SceneEngine app/routers/logs.py     POST /api/v1/log
  src/components/     ← UI (chat, admin, stage)    app/config.py           pydantic-settings ← .env
```

- **API-first**: the frontend talks to ONE JSON API (`/api/v1`) and never knows the avatar
  vendor. `POST /session` returns `{provider, personaName, sessionToken}` — the `provider`
  field picks the adapter. Swap Anam → Azure → Unreal = backend config + one new adapter file.
- **Media plane**: avatar video/audio flows browser ⇄ vendor over WebRTC, never through
  this backend. Current adapters: `AnamProvider` (live, Anam.ai free tier),
  `DryRunProvider` (rehearsal, browser TTS, zero avatar minutes).
- **Secrets** live in `backend/.env` only (gitignored; copy `.env.example`).
- Upgrade paths (hands/gestures, photoreal options): [docs/FREE_AVATAR_UPGRADE_PLAN.md](docs/FREE_AVATAR_UPGRADE_PLAN.md).

## Run

```bash
# backend (port 8123 — 8080 is taken by Apache on this machine)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8123

# frontend — dev (hot reload, API proxied to 8123)
cd frontend
npm install
npm run dev          # http://localhost:5173

# frontend — demo build (then FastAPI serves it at http://localhost:8123)
npm run build
```

If the frontend is hosted separately from the backend, set `VITE_API_BASE` at build time.

## The screens

| URL | What it is |
|---|---|
| `http://localhost:8123` | **The demo** — voice mode: speak a question, keyword routing answers |
| `?autoplay=1` | Script runs itself (question bubble → spoken answer → next); `?autoplay=3500` sets gap ms |
| `?provider=dry` (or `?dry=1`) | Rehearsal without the avatar — browser TTS voice, no free minutes used |
| `?admin=1` | **Script Manager** — edit questions/answers/keywords, enable/disable, add, save |
| `/docs` | FastAPI Swagger UI — all endpoints, schemas, try-it-out (the "backend areas" demo) |
| `?debug=1` | Shows the scene-jump counter (hidden for clean recordings) |

Always available in the demo: keys **1–6** jump to a scene, **Space**/mic-click advances,
⟳ Reconnect appears if the free tier's 3-minute session cap hits. The waveform under the
status is driven by the avatar's **real audio amplitude** when live (CSS fallback in dry mode).

## Editing the questions & answers

Two ways, no coding either way:

1. **Admin panel** — `http://localhost:8123/?admin=1`: edit, toggle, add, save.
2. **The file** — `backend/scenes.json`: plain JSON, re-read on every browser refresh
   (no restart). All 6 scenes currently enabled. Cards (icons/key-values/chips) are
   edited here. Plain-language guide: [backend/HOW_TO_ADD_QUESTIONS.md](backend/HOW_TO_ADD_QUESTIONS.md).

Saving through the admin panel validates input (empty question/answer rejected) and can
never corrupt the file.

## Coding standards

Documented as project skills (auto-loaded by Claude Code) in `.claude/skills/`:
**react-standards** (naming, structure, hooks, security) and **python-standards**
(PEP 8, FastAPI patterns, security). Enforcement — must pass before commit:

```bash
cd backend  && python -m ruff check . && python -m ruff format --check .
cd frontend && npm run lint && npm run build
```

## Demo timing note

Full 6-scene run ≈ 2:20–2:30 — inside the Anam free tier's 3-minute session cap but tight.
If a live take overruns, shorten gaps with `?autoplay=1500`. Free tier: 30 avatar
minutes/month; rehearse in dry mode.
