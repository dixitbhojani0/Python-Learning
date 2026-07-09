import { UserRound } from "lucide-react";
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
import type { ChatMessage, Scene, Status } from "./types";

const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
let msgId = 0;

export default function App() {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [scriptError, setScriptError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [playedOrder, setPlayedOrder] = useState<number[]>([]);
  const [personaName, setPersonaName] = useState("Penny");
  const [closing, setClosing] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [canReconnect, setCanReconnect] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const providerRef = useRef<AvatarProvider | null>(null);
  const engineRef = useRef<SceneEngine | null>(null);

  const addMessage = useCallback((role: ChatMessage["role"], text: string, card?: ChatMessage["card"]) => {
    setMessages((prev) => [...prev, { id: ++msgId, role, text, time: now(), card }]);
  }, []);

  useEffect(() => {
    fetchScript()
      .then(setScenes)
      .catch((e) => {
        logToServer("script fetch failed:", e);
        setScriptError("Couldn't load the demo script from the backend.");
      });
    const onError = (e: ErrorEvent) => logToServer("window.error:", e.message, `${e.filename}:${e.lineno}`);
    const onRejection = (e: PromiseRejectionEvent) => logToServer("unhandledrejection:", e.reason);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  const buildEngine = useCallback(
    (provider: AvatarProvider, sceneList: Scene[]) => {
      const engine = new SceneEngine(
        sceneList,
        provider,
        {
          onStatus: setStatus,
          onCaption: () => {}, // conversation panel shows the text; no subtitle overlay
          onBubble: (question) => addMessage("you", question),
          onScenePlayed: (i) => {
            setPlayedOrder((prev) => [...prev, i]);
            addMessage("assistant", sceneList[i].answer, sceneList[i].card);
          },
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
        addMessage("you", transcript);
        engine.routeTranscript(transcript);
      });
      provider.on("disconnected", () => {
        setStatus("idle");
        setCanReconnect(true);
      });
      return engine;
    },
    [addMessage],
  );

  const connect = useCallback(
    async (sceneList: Scene[]) => {
      setStatus("connecting");
      let provider: AvatarProvider;
      try {
        const session = config.forcedProvider ? { provider: config.forcedProvider } : await createSession();
        if (typeof session.personaName === "string") setPersonaName(session.personaName);
        provider = createProvider(session.provider);
        const engine = buildEngine(provider, sceneList);
        await provider.connect(videoRef.current!, session.sessionToken ? session : undefined);
        setHasVideo(provider.capabilities.video);
        setStatus("listening");
        return engine;
      } catch (e) {
        logToServer("avatar connection failed:", e);
        addMessage("system", `Avatar unavailable (${e instanceof Error ? e.message : e}) — continuing without it.`);
        provider = new DryRunProvider();
        const engine = buildEngine(provider, sceneList);
        setHasVideo(false);
        setStatus("listening");
        return engine;
      }
    },
    [buildEngine, addMessage],
  );

  const start = useCallback(async () => {
    let sceneList = scenes;
    if (sceneList.length === 0) {
      // script failed to load earlier — retry before starting
      try {
        sceneList = await fetchScript();
        setScenes(sceneList);
        setScriptError(null);
      } catch (e) {
        logToServer("script retry failed:", e);
        setScriptError("Couldn't load the demo script — is the backend running? Click Start to retry.");
        return;
      }
    }
    setStarted(true);
    setCanReconnect(false);
    const engine = await connect(sceneList);
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
          <span className="advisor-chip">
            <UserRound className="icon" size={14} />
            Advisor
          </span>
        </div>
      </header>

      {started && (
        <Stage
          videoRef={videoRef}
          showVideo={hasVideo}
          status={status}
          personaName={personaName}
          messages={messages}
          onMicClick={() => void engineRef.current?.playScene(engineRef.current.nextUnplayed())}
        />
      )}

      {!started && <Landing note={landingNote} error={scriptError} onStart={() => void start()} />}
      {closing && <ClosingScreen />}
      {config.debug && started && (
        <SceneJumpList scenes={scenes} playedOrder={playedOrder} onJump={(i) => void engineRef.current?.playScene(i)} />
      )}
    </>
  );
}
