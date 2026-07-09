import { Mic, UserRoundSearch } from "lucide-react";
import type { RefObject } from "react";

import type { ChatMessage, Status } from "../types";
import { ConversationPanel } from "./ConversationPanel";

interface StageProps {
  videoRef: RefObject<HTMLVideoElement | null>;
  showVideo: boolean;
  status: Status;
  personaName: string;
  messages: ChatMessage[];
  onMicClick: () => void;
}

function statusLabel(status: Status, personaName: string): string {
  switch (status) {
    case "connecting":
      return "Connecting…";
    case "listening":
      return "Listening…";
    case "thinking":
      return "Searching knowledge base…";
    case "speaking":
      return `${personaName} is speaking`;
    default:
      return "Idle";
  }
}

export function Stage({ videoRef, showVideo, status, personaName, messages, onMicClick }: StageProps) {
  return (
    <main id="stage">
      <section id="avatar-pane">
        <video id="avatar-video" ref={videoRef} autoPlay playsInline />
        {!showVideo && (
          <div id="dry-placeholder">
            <div className="dry-face">
              <UserRoundSearch className="icon" size={110} strokeWidth={1.2} />
            </div>
            <div className="dry-label">DRY RUN — avatar not connected</div>
          </div>
        )}

        {/* speaking overlay: status + waveform, lower third of the avatar */}
        <div id="speak-overlay" data-state={status}>
          <span className="speak-label">{statusLabel(status, personaName)}</span>
          <div className="waveform" aria-hidden="true">
            {Array.from({ length: 24 }, (_, i) => (
              <i key={i} />
            ))}
          </div>
        </div>

        <button id="mic-btn" title="Speak — or click to advance to the next step" onClick={onMicClick}>
          <Mic size={24} />
        </button>
      </section>

      <ConversationPanel messages={messages} personaName={personaName} />
    </main>
  );
}
