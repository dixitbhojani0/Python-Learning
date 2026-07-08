# Free Avatar Upgrade Plan — hand gestures & beyond

**Requirement:** photoreal avatar + accurate lip sync + visible hand gestures, ideally live, ideally free.
**Hard truth (researched 2026-07):** no free option delivers all four at once — that combination is
exactly what Azure (~$0.50/min) and HeyGen (~$0.20/min) sell. Free options each give up one corner:

| Option | Photoreal | Hands/gestures | Live | Cost | Runs on this machine? |
|---|---|---|---|---|---|
| **Anam free tier** (current) | ✅ | ❌ head/shoulders only, no gesture API | ✅ | free 30 min/mo | ✅ |
| **TalkingHead** (met4citizen, MIT) | ❌ 3D game-style | ✅ `playGesture` + MotionEngine, full-body RPM avatars | ✅ | free, unlimited | ✅ browser/WebGL |
| **EchoMimicV2** (Ant Group, Apache-2.0) | ✅ from one reference photo | ✅ audio-driven half-body incl. hands | ❌ offline render | free software; needs CUDA GPU ≥12 GB VRAM | ❌ Intel iGPU only — needs cloud GPU (~$0.50/hr rental) |
| **MetaHuman + Audio2Face/ACE** (free licenses) | ✅ AAA | ✅ fully scripted | ✅ | free software; RTX GPU + weeks of UE work | ❌ no RTX; skillset cost dominates |

## Path A — TalkingHead provider (free, live, gestures; the only path that runs here today)

[met4citizen/TalkingHead](https://github.com/met4citizen/TalkingHead): browser JS class, real-time
lip sync on Ready Player Me **full-body** GLB avatars, Mixamo animations, `playGesture()` for hand
gestures ("index", "ok", "thumbup", waving…), moods/expressions, MotionEngine for advanced motion.

Fit with our architecture (this is why we built the provider layer):

1. New `frontend/src/providers/TalkingHeadProvider.ts`:
   - `connect(videoEl)` → mounts a Three.js canvas over the video slot, loads an RPM avatar GLB
     (create a business-attire avatar free at readyplayer.me).
   - `speak(text)` → TalkingHead `speakText`; lip-sync visemes from its English module.
     Gestures: scene JSON gains an optional `gesture` field → `playGesture(name)` fires as the line starts.
   - `capabilities: { video: false, listens: false }` (canvas instead of video; mic via Web Speech API if wanted).
2. One line in `providers/index.ts` registry + `AVATAR_PROVIDER=talkinghead` branch in `session.py` (no token needed → session returns `{"provider":"talkinghead"}`).
3. TTS: built-in default is Google Cloud TTS (free tier ~1M chars/mo, needs a key); fallback = browser speechSynthesis (what DryRun uses) with approximate viseme timing.
4. `backend` script JSON: add `"gesture": "index"` etc. per scene — pointing at cards on cue becomes possible, which even Azure live can't do.

**Effort:** ~half a day. **Trade-off:** the face is a stylized 3D character, not the photoreal human of Anam/Azure.

## Path B — EchoMimicV2 pre-rendered scenes (free software, photoreal + hands, not live)

[EchoMimicV2](https://github.com/antgroup/echomimic_v2): give it one reference photo + the scene's
audio → photoreal half-body video **with natural hand gestures**. Generate the 6 scene videos once,
then a trivial `PrerenderedVideoProvider` plays the matching MP4 per scene (`speak()` = play video,
resolve on `ended`). The audience of a recorded demo cannot tell it isn't live.

Blocker on this machine: needs a CUDA GPU (≥12 GB VRAM). Workaround: rent a cloud GPU
(RunPod/Vast.ai RTX 4090 ≈ $0.40–0.70/hr; all 6 scenes render in an hour or two ≈ **$1–2 one-time**).
Audio: generate free with Edge TTS (`edge-tts` Python pkg, natural neural voices, free).

**Effort:** ~a day incl. cloud GPU setup. **Trade-off:** fixed script only — changing a line means re-rendering that scene.

## Path C — paid live (reference)

Azure TTS Avatar real-time: photoreal + hands visible + natural autonomous gesturing, live —
`AzureProvider.ts` + `session.py` branch, ~$0.50/min. The moment budget exists, this is the
straight upgrade; nothing else in the app changes.

## Recommendation

- Demo must stay **live and free** → **Path A (TalkingHead)** — accepts the game-style look, gains on-cue pointing at cards.
- Demo is **recorded anyway** and photoreal + hands matters most → **Path B (EchoMimicV2 via ~$1–2 of rented GPU)**.
- Budget appears → **Path C (Azure)**.

All three are one provider adapter + one backend branch — the current architecture was built for exactly this swap.
