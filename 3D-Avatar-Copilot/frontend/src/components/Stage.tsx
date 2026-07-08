import type { RefObject } from "react";

import type { Scene, Status } from "../types";
import { CardRenderer } from "./CardRenderer";

const STATUS_LABEL: Record<Status, string> = {
  idle: "Idle",
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Searching knowledge base…",
  speaking: "Speaking",
};

interface StageProps {
  videoRef: RefObject<HTMLVideoElement | null>;
  showVideo: boolean;
  status: Status;
  caption: string | null;
  bubble: string | null;
  scenes: Scene[];
  playedOrder: number[];
  onMicClick: () => void;
}

export function Stage({ videoRef, showVideo, status, caption, bubble, scenes, playedOrder, onMicClick }: StageProps) {
  const activeIdx = playedOrder[playedOrder.length - 1];
  return (
    <main id="stage">
      <section id="avatar-pane">
        <video id="avatar-video" ref={videoRef} autoPlay playsInline />
        {!showVideo && (
          <div id="dry-placeholder">
            <div className="dry-face">👩‍💼</div>
            <div className="dry-label">DRY RUN — avatar not connected</div>
          </div>
        )}

        {bubble && <div id="user-bubble">{bubble}</div>}
        {caption && <div id="caption">{caption}</div>}

        <div id="controls">
          <div id="status-pill" data-state={status}>
            <span className="dot" />
            <span>{STATUS_LABEL[status]}</span>
          </div>
          <div id="waveform" aria-hidden="true">
            {Array.from({ length: 9 }, (_, i) => (
              <i key={i} />
            ))}
          </div>
          <button id="mic-btn" title="Speak — or click to advance to the next step" onClick={onMicClick}>
            🎤
          </button>
        </div>
      </section>

      <aside id="cards-pane">
        <div id="cards">
          {playedOrder.map(
            (idx) =>
              scenes[idx]?.card && <CardRenderer key={idx} card={scenes[idx].card} active={idx === activeIdx} />,
          )}
        </div>
      </aside>
    </main>
  );
}
