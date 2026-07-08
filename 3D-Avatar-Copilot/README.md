# FinAdvisor AI — 3D Avatar Advisor Copilot

A financial-advisor meeting-prep demo: a photorealistic talking avatar answers scripted
questions with lip sync, while glassmorphism cards (client snapshot, knowledge sources,
compliance, follow-up note) animate alongside.

## Architecture

- **frontend/** — React 19 + TypeScript + Vite. Talks to ONE JSON API (`/api/v1`); the
  avatar vendor is hidden behind the `AvatarProvider` interface (`src/providers/`).
  Adapters: `AnamProvider` (live), `DryRunProvider` (rehearsal). Azure TTS Avatar or
  Unreal MetaHuman pixel-streaming plug in later as new adapters — UI and scene engine
  never change.
- **backend/** — FastAPI. `POST /api/v1/session` (provider session bootstrap),
  `GET /api/v1/script` (scenes as structured JSON — future RAG/LLM slot),
  `POST /api/v1/log` (client error reporting). Secrets in `backend/.env`
  (copy `.env.example`). Media (video/audio) flows browser ⇄ vendor over WebRTC,
  never through this backend.

## Run

```bash
# backend (port 8123 — 8080 is taken by Apache on this machine)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8123

# frontend — dev
cd frontend
npm install
npm run dev          # http://localhost:5173

# frontend — demo build (served by FastAPI at http://localhost:8123)
npm run build
```

## Demo modes (URL params)

| URL param | Effect |
|---|---|
| *(none)* | Voice mode: speak, keyword routing answers |
| `?autoplay=1` | Script runs itself (advisor bubble → answer → next); `?autoplay=3500` sets the gap in ms |
| `?provider=dry` (or `?dry=1`) | Rehearsal: no avatar connection, browser TTS voice, no free minutes used |
| `?debug=1` | Shows the scene-jump counter (hidden for clean recordings) |

Always available: keys **1–6** jump to a scene, **Space**/mic-click advances, ⟳ Reconnect
appears if the free-tier 3-minute session cap hits.

## Editing the questions & answers

Everything the avatar says lives in **`backend/scenes.json`** — plain JSON, editable by
anyone, re-read on every browser refresh (no restart). Each scene has an
`"enabled": true/false` switch (2 of 6 currently enabled). Plain-language guide:
[backend/HOW_TO_ADD_QUESTIONS.md](backend/HOW_TO_ADD_QUESTIONS.md).
