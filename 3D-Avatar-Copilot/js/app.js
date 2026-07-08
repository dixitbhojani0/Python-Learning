// FinAdvisor AI — Advisor Copilot. Scene engine + Anam avatar client.
import { SCENES } from "./script-data.js";

const params = new URLSearchParams(location.search);
const DRY = params.has("dry");
// ?autoplay=1 runs the whole script scene by scene; ?autoplay=3000 sets the gap in ms
const AUTOPLAY = params.has("autoplay");
const AUTOPLAY_GAP = Number(params.get("autoplay")) || 2000;
const DEBUG = params.has("debug"); // shows the scene-jump counter (hidden for clean recordings)
const SHOW_CLOSING = false; // set true to re-enable the end-of-demo value screen

const $ = (id) => document.getElementById(id);
const els = {
  landing: $("landing"), landingNote: $("landing-note"), startBtn: $("start-btn"),
  stage: $("stage"), video: $("avatar-video"), dryPlaceholder: $("dry-placeholder"),
  bubble: $("user-bubble"), caption: $("caption"),
  statusPill: $("status-pill"), statusText: $("status-text"),
  micBtn: $("mic-btn"), cards: $("cards"), closing: $("closing"),
  sceneList: $("scene-list"), reconnectBtn: $("reconnect-btn"),
};

const STATUS_LABEL = {
  idle: "Idle",
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Searching knowledge base…",
  speaking: "Speaking",
};

let anamClient = null;
let played = SCENES.map(() => false);
let busy = false;

// ---------- UI helpers ----------

function setStatus(state) {
  els.statusPill.dataset.state = state;
  els.statusText.textContent = STATUS_LABEL[state];
  document.body.classList.toggle("speaking", state === "speaking");
}

function showBubble(text) {
  els.bubble.textContent = text;
  els.bubble.classList.remove("hidden");
  clearTimeout(showBubble.t);
  showBubble.t = setTimeout(() => els.bubble.classList.add("hidden"), 6000);
}

function showCaption(text) {
  els.caption.textContent = text;
  els.caption.classList.remove("hidden");
}

function addCard(html) {
  document.querySelectorAll(".card.active").forEach((c) => c.classList.remove("active"));
  const card = document.createElement("div");
  card.className = "card active";
  card.innerHTML = html;
  els.cards.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

function logToServer(...parts) {
  const msg = parts.map((p) => (p instanceof Error ? p.stack || p.message : typeof p === "object" ? JSON.stringify(p) : String(p))).join(" ");
  fetch("/api/log", { method: "POST", body: new Date().toISOString() + " " + msg }).catch(() => {});
}
window.addEventListener("error", (e) => logToServer("window.error:", e.message, e.filename + ":" + e.lineno));
window.addEventListener("unhandledrejection", (e) => logToServer("unhandledrejection:", e.reason));

// ---------- Scene engine ----------

function nextUnplayed() {
  return played.findIndex((p) => !p);
}

function routeTranscript(text) {
  const t = text.toLowerCase();
  let idx = SCENES.findIndex((s, i) => !played[i] && s.triggers.some((k) => t.includes(k)));
  if (idx === -1) idx = nextUnplayed(); // linear script: never stall on a misheard phrase
  if (idx !== -1) playScene(idx);
}

async function playScene(i) {
  if (busy || i < 0) return;
  busy = true;
  played[i] = true;
  const scene = SCENES[i];
  els.sceneList.children[i]?.classList.add("played");

  if (scene.searching) {
    setStatus("thinking");
    await delay(1300);
  }
  if (scene.cardHTML) addCard(scene.cardHTML);

  setStatus("speaking");
  showCaption(scene.spokenText);

  if (DRY || !anamClient) {
    // rehearsal voice: browser TTS so dry runs are audible (live mode uses the avatar's voice)
    await new Promise((resolve) => {
      const est = (scene.spokenText.split(" ").length / 2.6) * 1000;
      setTimeout(resolve, est * 1.8); // fallback if TTS is unavailable (e.g. headless)
      if ("speechSynthesis" in window) {
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(scene.spokenText);
        u.onend = u.onerror = resolve;
        speechSynthesis.speak(u);
      }
    });
    onSpeechDone();
  } else {
    try {
      await anamClient.talk(scene.spokenText);
    } catch (e) {
      console.error("talk() failed:", e);
      onSpeechDone();
    }
    // safety net if the persona-finished event never arrives
    clearTimeout(playScene.t);
    playScene.t = setTimeout(onSpeechDone, (scene.spokenText.split(" ").length / 2.0) * 1000 + 4000);
  }

  if (scene.label === "Closing" && SHOW_CLOSING) {
    setTimeout(showClosing, DRY ? 500 : 3500);
  }
  busy = false;
}

let speechDoneResolver = null;

function onSpeechDone() {
  els.caption.classList.add("hidden");
  setStatus("listening");
  speechDoneResolver?.();
  speechDoneResolver = null;
}

// ---------- Autoplay: the script runs itself, scene by scene ----------

async function runAutoplay() {
  // wait until the session is ready (or dry mode set us listening)
  while (els.statusPill.dataset.state !== "listening") await delay(300);
  for (let i = 0; i < SCENES.length; i++) {
    showBubble(SCENES[i].advisorLine);
    await delay(1600); // let the audience read the advisor's question
    const finished = new Promise((r) => (speechDoneResolver = r));
    playScene(i);
    await finished;
    await delay(AUTOPLAY_GAP);
  }
}

function showClosing() {
  els.closing.classList.remove("hidden");
  setStatus("idle");
  anamClient?.stopStreaming?.(); // stop billing free minutes
}

// ---------- Anam connection ----------

async function connect() {
  setStatus("connecting");
  let token;
  try {
    const resp = await fetch("/api/session-token", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    token = data.sessionToken;
  } catch (e) {
    console.warn("No avatar session — falling back to dry run.", e);
    logToServer("token fetch failed:", e);
    els.dryPlaceholder.classList.remove("hidden");
    showCaption(`Avatar unavailable (${e.message}) — running in dry mode. Press 1–6 or Space.`);
    setTimeout(() => els.caption.classList.add("hidden"), 5000);
    setStatus("listening");
    return;
  }

  try {
    const { createClient, AnamEvent } = await import("https://esm.sh/@anam-ai/js-sdk@latest");
    anamClient = createClient(token);

    let videoStarted = false;
    anamClient.addListener(AnamEvent.VIDEO_PLAY_STARTED, () => { videoStarted = true; logToServer("avatar video started"); });
    anamClient.addListener(AnamEvent.SESSION_READY, () => setStatus("listening"));
    anamClient.addListener(AnamEvent.CONNECTION_CLOSED, (reason) => {
      console.warn("Anam connection closed:", reason);
      setStatus("idle");
      els.reconnectBtn.classList.remove("hidden"); // 3-min free-tier cap hit? one click restarts
    });
    anamClient.addListener(AnamEvent.MESSAGE_HISTORY_UPDATED, (messages) => {
      const last = messages[messages.length - 1];
      if (!last) return;
      if (last.role === "user") {
        if (AUTOPLAY) return; // autoplay drives the script; mic input is ignored
        showBubble(last.content);
        routeTranscript(last.content);
      } else {
        clearTimeout(playScene.t);
        onSpeechDone();
      }
    });

    await anamClient.streamToVideoElement("avatar-video");
    setTimeout(() => {
      if (!videoStarted) {
        showCaption("⚠ Avatar video hasn't started. WebRTC may be blocked by your network/VPN — check the browser console (F12) and try a different network.");
      }
    }, 12000);
  } catch (e) {
    console.error("Avatar connection failed:", e);
    logToServer("avatar connection failed:", e);
    anamClient = null;
    els.dryPlaceholder.classList.remove("hidden");
    showCaption("⚠ Avatar error: " + (e.message || e) + " — continuing without avatar.");
    setStatus("listening");
  }
}

// ---------- Wiring ----------

function buildSceneList() {
  SCENES.forEach((s, i) => {
    const li = document.createElement("li");
    li.textContent = i + 1;
    li.title = s.label;
    li.onclick = () => playScene(i);
    els.sceneList.appendChild(li);
  });
}

els.startBtn.onclick = async () => {
  els.landing.classList.add("hidden");
  els.stage.classList.remove("hidden");
  if (DRY) {
    els.dryPlaceholder.classList.remove("hidden");
    setStatus("listening");
  } else {
    await connect();
  }
  if (AUTOPLAY) runAutoplay();
};

els.micBtn.onclick = () => playScene(nextUnplayed()); // click fallback: advance the script
els.reconnectBtn.onclick = async () => {
  els.reconnectBtn.classList.add("hidden");
  await connect();
};

document.addEventListener("keydown", (e) => {
  if (e.key >= "1" && e.key <= "6") playScene(Number(e.key) - 1);
  if (e.key === " " && !els.stage.classList.contains("hidden")) {
    e.preventDefault();
    playScene(nextUnplayed());
  }
});

if (DRY) els.landingNote.textContent = "Dry run — avatar disconnected, no free minutes used.";
if (AUTOPLAY) els.landingNote.textContent += " Autoplay — the script runs itself after Start.";
buildSceneList();
if (!DEBUG) els.sceneList.classList.add("hidden"); // keys 1-6 and Space still work invisibly
setStatus("idle");

// ?auto=N: smoke-test hook — starts the demo and plays scene N (1-6)
const auto = params.get("auto");
if (auto !== null) {
  els.startBtn.click();
  if (!AUTOPLAY) setTimeout(() => playScene((Number(auto) || 1) - 1), 600);
}
