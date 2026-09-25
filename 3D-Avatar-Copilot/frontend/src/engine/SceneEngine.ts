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
  /** Autoplay awaits this — return a promise that resolves when the question has been presented (e.g. spoken). */
  onBubble: (text: string) => void | Promise<void>;
  onScenePlayed: (index: number) => void;
  /** Spoken input matched nothing — a polite clarification is being given. */
  onFallback: (text: string) => void;
  onClosing: () => void;
}

const FALLBACK_REPLY = "I'm sorry, I didn't quite catch that. Could you rephrase your question?";

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

  /** Advisor speech → first unplayed scene whose keyword matches; no match → polite clarification (never a wrong answer). */
  routeTranscript(transcript: string): void {
    const t = transcript.toLowerCase();
    const idx = this.scenes.findIndex((s, i) => !this.played[i] && s.keywords.some((k) => t.includes(k)));
    if (idx !== -1) void this.playScene(idx);
    else void this.playFallbackReply();
  }

  private async playFallbackReply(): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      this.callbacks.onFallback(FALLBACK_REPLY);
      this.callbacks.onStatus("speaking");
      try {
        await this.provider.speak(FALLBACK_REPLY);
      } finally {
        this.callbacks.onStatus("listening");
      }
    } finally {
      this.busy = false;
    }
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
      this.callbacks.onCaption(scene.answer);
      try {
        await this.provider.speak(scene.answer);
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

  /** The script runs itself: question (spoken + streamed) → beat → answer → gap → next scene. */
  async runAutoplay(gapMs: number): Promise<void> {
    for (let i = 0; i < this.scenes.length; i++) {
      await this.callbacks.onBubble(this.scenes[i].question);
      await delay(600); // beat between question and answer
      await this.playScene(i);
      await delay(gapMs);
    }
  }
}
