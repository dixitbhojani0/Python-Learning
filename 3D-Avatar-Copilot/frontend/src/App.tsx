import { useCallback, useEffect, useRef, useState } from "react";

import { createSession, fetchScript, logToServer } from "./api/client";
import { ClosingScreen } from "./components/ClosingScreen";
import { Landing } from "./components/Landing";
import { SceneJumpList } from "./components/SceneJumpList";
import { Stage } from "./components/Stage";
import { config } from "./config";
import { SceneEngine } from "./engine/SceneEngine";
import { createProvider } from "./providers";
import type { AvatarProvider } from "./providers/AvatarProvider";
import { DryRunProvider } from "./providers/DryRunProvider";
import type { Scene, Status } from "./types";

export default function App() {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [started, setStarted] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [caption, setCaption] = useState<string | null>(null);
  const [bubble, setBubble] = useState<string | null>(null);
  const [playedOrder, setPlayedOrder] = useState<number[]>([]);
  const [closing, setClosing] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [canReconnect, setCanReconnect] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const providerRef = useRef<AvatarProvider | null>(null);
  const engineRef = useRef<SceneEngine | null>(null);
  const bubbleTimer = useRef<number | undefined>(undefined);

  const showBubble = useCallback((text: string) => {
    setBubble(text);
    clearTimeout(bubbleTimer.current);
    bubbleTimer.current = window.setTimeout(() => setBubble(null), 6000);
  }, []);

  useEffect(() => {
    fetchScript()
      .then(setScenes)
      .catch((e) => logToServer("script fetch failed:", e));
    const onError = (e: ErrorEvent) => logToServer("window.error:", e.message, `${e.filename}:${e.lineno}`);
    const onRejection = (e: PromiseRejectionEvent) => logToServer("unhandledrejection:", e.reason);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  // the waveform animates via body.speaking (see global.css)
  useEffect(() => {
    document.body.classList.toggle("speaking", status === "speaking");
  }, [status]);

  const buildEngine = useCallback(
    (provider: AvatarProvider, sceneList: Scene[]) => {
      const engine = new SceneEngine(
        sceneList,
        provider,
        {
          onStatus: setStatus,
          onCaption: setCaption,
          onBubble: showBubble,
          onScenePlayed: (i) => setPlayedOrder((prev) => [...prev, i]),
          onClosing: () => {
            setClosing(true);
            provider.disconnect(); // stop billing avatar minutes
          },
        },
        config.showClosing,
      );
      engineRef.current = engine;
      providerRef.current = provider;
      provider.on("userSpeech", (transcript) => {
        if (config.autoplay) return; // autoplay drives the script; mic input is ignored
        showBubble(transcript);
        engine.routeTranscript(transcript);
      });
      provider.on("disconnected", () => {
        setStatus("idle");
        setCanReconnect(true);
      });
      return engine;
    },
    [showBubble],
  );

  const connect = useCallback(
    async (sceneList: Scene[]) => {
      setStatus("connecting");
      let provider: AvatarProvider;
      try {
        const session = config.forcedProvider ? { provider: config.forcedProvider } : await createSession();
        provider = createProvider(session.provider);
        const engine = buildEngine(provider, sceneList);
        await provider.connect(videoRef.current!, session.sessionToken ? session : undefined);
        setHasVideo(provider.capabilities.video);
        setStatus("listening");
        return engine;
      } catch (e) {
        logToServer("avatar connection failed:", e);
        setCaption(`⚠ Avatar error: ${e instanceof Error ? e.message : e} — continuing without avatar.`);
        setTimeout(() => setCaption(null), 5000);
        provider = new DryRunProvider();
        const engine = buildEngine(provider, sceneList);
        setHasVideo(false);
        setStatus("listening");
        return engine;
      }
    },
    [buildEngine],
  );

  const start = useCallback(async () => {
    setStarted(true);
    setCanReconnect(false);
    const engine = await connect(scenes);
    if (config.autoplay) void engine.runAutoplay(config.autoplayGapMs);
  }, [connect, scenes]);

  // keyboard fallbacks: 1-6 jump, Space = next unplayed
  useEffect(() => {
    if (!started) return;
    const onKey = (e: KeyboardEvent) => {
      const engine = engineRef.current;
      if (!engine) return;
      if (e.key >= "1" && e.key <= "6") void engine.playScene(Number(e.key) - 1);
      if (e.key === " ") {
        e.preventDefault();
        void engine.playScene(engine.nextUnplayed());
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [started]);

  // ?auto=N smoke-test hook
  const autoFired = useRef(false);
  useEffect(() => {
    if (config.auto === null || autoFired.current || scenes.length === 0) return;
    autoFired.current = true;
    void start().then(() => {
      if (!config.autoplay) setTimeout(() => void engineRef.current?.playScene((Number(config.auto) || 1) - 1), 600);
    });
  }, [scenes, start]);

  const landingNote = [
    config.forcedProvider === "dry" ? "Dry run — avatar disconnected, no free minutes used." : "",
    config.autoplay ? "Autoplay — the script runs itself after Start." : "",
  ]
    .join(" ")
    .trim();

  return (
    <>
      <header id="topbar">
        <div className="brand">
          FinAdvisor <span className="brand-ai">AI</span>
          <span className="brand-sub">Advisor Copilot</span>
        </div>
        <div className="topbar-right">
          {canReconnect && (
            <button className="ghost-btn" title="Restart the avatar session" onClick={() => void start()}>
              ⟳ Reconnect
            </button>
          )}
          <span className="advisor-chip">👤 Advisor</span>
        </div>
      </header>

      {started && (
        <Stage
          videoRef={videoRef}
          showVideo={hasVideo}
          status={status}
          caption={caption}
          bubble={bubble}
          scenes={scenes}
          playedOrder={playedOrder}
          onMicClick={() => void engineRef.current?.playScene(engineRef.current.nextUnplayed())}
        />
      )}

      {!started && <Landing note={landingNote} onStart={() => void start()} />}
      {closing && <ClosingScreen />}
      {config.debug && started && (
        <SceneJumpList scenes={scenes} playedOrder={playedOrder} onJump={(i) => void engineRef.current?.playScene(i)} />
      )}
    </>
  );
}
