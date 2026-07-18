import { Mic, MicOff, UserRoundSearch, Volume2, VolumeX } from "lucide-react";
import type { RefObject } from "react";

import type { ChatMessage, Status } from "../types";
import { ConversationPanel } from "./ConversationPanel";
import { LiveWaveform } from "./LiveWaveform";

interface StageProps {
  videoRef: RefObject<HTMLVideoElement | null>;
  showVideo: boolean;
  connecting: boolean;
  dryMode: boolean;
  status: Status;
  personaName: string;
  messages: ChatMessage[];
  micMuted: boolean;
  outputMuted: boolean;
  canListen: boolean;
  onToggleMic: () => void;
  onToggleOutput: () => void;
}

function statusLabel(status: Status, personaName: string, micMuted: boolean): string {
  switch (status) {
    case "connecting":
      return "Connecting…";
    case "listening":
      return micMuted ? "Microphone muted" : "Listening…";
    case "thinking":
      return "Searching knowledge base…";
    case "speaking":
      return `${personaName} is speaking`;
    default:
      return "Idle";
  }
}

export function Stage({
  videoRef,
  showVideo,
  connecting,
  dryMode,
  status,
  personaName,
  messages,
  micMuted,
  outputMuted,
  canListen,
  onToggleMic,
  onToggleOutput,
}: StageProps) {
  return (
    <main id="stage">
      <section id="avatar-pane">
        <video id="avatar-video" ref={videoRef} autoPlay playsInline />
        {!showVideo && (
          <div id="dry-placeholder">
            <div className={connecting ? "dry-face pulse" : "dry-face"}>
              <UserRoundSearch className="icon" size={110} strokeWidth={1.2} />
            </div>
            {connecting ? (
              <div className="connecting-label">Connecting your copilot…</div>
            ) : dryMode ? (
              <div className="dry-label">DRY RUN — avatar not connected</div>
            ) : null}
          </div>
        )}

        {/* speaking overlay: status + waveform, lower third of the avatar */}
        <div id="speak-overlay" data-state={status}>
          <span className="speak-label">{statusLabel(status, personaName, micMuted)}</span>
          <LiveWaveform videoRef={videoRef} live={showVideo} />
        </div>

        <div id="call-controls">
          <button
            className={micMuted ? "control-btn off" : "control-btn"}
            title={canListen ? (micMuted ? "Unmute microphone" : "Mute microphone") : "Microphone unavailable"}
            disabled={!canListen}
            onClick={onToggleMic}
          >
            {micMuted ? <MicOff size={22} /> : <Mic size={22} />}
          </button>
          <button
            className={outputMuted ? "control-btn off" : "control-btn"}
            title={outputMuted ? "Unmute voice" : "Mute voice"}
            onClick={onToggleOutput}
          >
            {outputMuted ? <VolumeX size={22} /> : <Volume2 size={22} />}
          </button>
        </div>
      </section>

      <ConversationPanel messages={messages} personaName={personaName} status={status} />
    </main>
  );
}
