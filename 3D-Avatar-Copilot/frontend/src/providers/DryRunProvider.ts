/** Rehearsal provider: browser TTS voice, no connection, no avatar minutes used. */
import { ProviderEventEmitter, type AvatarProvider } from "./AvatarProvider";

export class DryRunProvider extends ProviderEventEmitter implements AvatarProvider {
  readonly capabilities = { video: false, listens: false };

  async connect(): Promise<void> {
    this.emit("ready");
  }

  async speak(text: string): Promise<void> {
    this.emit("speakingStart");
    await new Promise<void>((resolve) => {
      const estMs = (text.split(" ").length / 2.6) * 1000;
      setTimeout(resolve, estMs * 1.8); // fallback if TTS is unavailable (e.g. headless)
      if ("speechSynthesis" in window) {
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
