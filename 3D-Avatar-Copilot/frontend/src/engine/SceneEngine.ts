/**
 * Framework-agnostic scene engine: keyword routing, busy-guard, autoplay loop.
 * Talks only to AvatarProvider; reports to the UI through callbacks — never
 * touches the DOM, so it ports to any framework unchanged.
 */
import type { AvatarProvider } from "../providers/AvatarProvider";
import type { Scene, Status } from "../types";

export interface EngineCallbacks {
  onStatus: (status: Status) => void;
  onCaption: (text: string | null) => void;
  onBubble: (text: string) => void;
  onScenePlayed: (index: number) => void;
  onClosing: () => void;
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class SceneEngine {
  private scenes: Scene[];
  private provider: AvatarProvider;
  private callbacks: EngineCallbacks;
  private showClosing: boolean;
  private played: boolean[];
  private busy = false;

  constructor(scenes: Scene[], provider: AvatarProvider, callbacks: EngineCallbacks, showClosing: boolean) {
    this.scenes = scenes;
    this.provider = provider;
    this.callbacks = callbacks;
    this.showClosing = showClosing;
    this.played = scenes.map(() => false);
  }

  nextUnplayed(): number {
    return this.played.findIndex((p) => !p);
  }

  /** Advisor speech → first unplayed scene whose trigger matches; no match → next unplayed (linear script never stalls). */
  routeTranscript(transcript: string): void {
    const t = transcript.toLowerCase();
    let idx = this.scenes.findIndex((s, i) => !this.played[i] && s.triggers.some((k) => t.includes(k)));
    if (idx === -1) idx = this.nextUnplayed();
    if (idx !== -1) void this.playScene(idx);
  }

  async playScene(index: number): Promise<void> {
    if (this.busy || index < 0 || index >= this.scenes.length) return;
    this.busy = true;
    try {
      const scene = this.scenes[index];
      this.played[index] = true;

      if (scene.searching) {
        this.callbacks.onStatus("thinking");
        await delay(1300);
      }
      this.callbacks.onScenePlayed(index);

      this.callbacks.onStatus("speaking");
      this.callbacks.onCaption(scene.spokenText);
      try {
        await this.provider.speak(scene.spokenText);
      } finally {
        this.callbacks.onCaption(null);
        this.callbacks.onStatus("listening");
      }

      if (scene.closing && this.showClosing) {
        setTimeout(() => this.callbacks.onClosing(), 500);
      }
    } finally {
      this.busy = false;
    }
  }

  /** The script runs itself: advisor bubble → answer → gap → next scene. */
  async runAutoplay(gapMs: number): Promise<void> {
    for (let i = 0; i < this.scenes.length; i++) {
      this.callbacks.onBubble(this.scenes[i].advisorLine);
      await delay(1600); // let the audience read the advisor's question
      await this.playScene(i);
      await delay(gapMs);
    }
  }
}
