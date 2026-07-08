import type { Scene } from "../types";

/** Discreet live-demo insurance: visible only with ?debug=1 (keys 1-6 always work). */
export function SceneJumpList({
  scenes,
  playedOrder,
  onJump,
}: {
  scenes: Scene[];
  playedOrder: number[];
  onJump: (index: number) => void;
}) {
  return (
    <ol id="scene-list" title="Jump to scene (or press 1–6)">
      {scenes.map((s, i) => (
        <li key={s.id} className={playedOrder.includes(i) ? "played" : ""} title={s.topic} onClick={() => onJump(i)}>
          {i + 1}
        </li>
      ))}
    </ol>
  );
}
