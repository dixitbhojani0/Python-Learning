/** Rehearsal provider: browser TTS voice, no connection, no avatar minutes used. */
import { ProviderEventEmitter, type AvatarProvider } from "./AvatarProvider";

export class DryRunProvider extends ProviderEventEmitter implements AvatarProvider {
  readonly capabilities = { video: false, listens: false };

  private outputMuted = false;

  async connect(): Promise<void> {
    this.emit("ready");
  }

  setOutputMuted(muted: boolean): void {
    this.outputMuted = muted;
    if (muted && "speechSynthesis" in window) speechSynthesis.cancel();
  }

  async speak(text: string): Promise<void> {
    this.emit("speakingStart");
    await new Promise<void>((resolve) => {
      const estMs = (text.split(" ").length / 2.6) * 1000;
      setTimeout(resolve, estMs * 1.8); // fallback if TTS is unavailable (e.g. headless)
      if ("speechSynthesis" in window && !this.outputMuted) {
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.onend = u.onerror = () => resolve();
        speechSynthesis.speak(u);
      }
    });
    this.emit("speakingEnd");
  }

  disconnect(): void {
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  }
}
