/** Anam.ai implementation: session token from our backend, media over WebRTC. */
import { AnamEvent, createClient, type AnamClient } from "@anam-ai/js-sdk";

import { createSession } from "../api/client";
import type { SessionInfo } from "../types";
import { ProviderEventEmitter, type AvatarProvider } from "./AvatarProvider";

export class AnamProvider extends ProviderEventEmitter implements AvatarProvider {
  readonly capabilities = { video: true, listens: true };

  private client: AnamClient | null = null;
  private speakingEndResolver: (() => void) | null = null;
  private safetyTimer: number | undefined;

  async connect(videoEl: HTMLVideoElement, session?: SessionInfo): Promise<void> {
    session ??= await createSession();
    if (!session.sessionToken) throw new Error("backend returned no sessionToken");

    this.client = createClient(session.sessionToken);

    this.client.addListener(AnamEvent.SESSION_READY, () => this.emit("ready"));
    this.client.addListener(AnamEvent.CONNECTION_CLOSED, () => this.emit("disconnected"));
    this.client.addListener(AnamEvent.MESSAGE_HISTORY_UPDATED, (messages) => {
      const last = messages[messages.length - 1];
      if (!last) return;
      if (last.role === "user") {
        this.emit("userSpeech", last.content);
      } else {
        this.finishSpeaking(); // persona turn completed
      }
    });

    if (!videoEl.id) videoEl.id = "avatar-video";
    await this.client.streamToVideoElement(videoEl.id);

    // graceful start: resolve only when the avatar video is actually playing,
    // so the conversation never begins against a black/loading pane
    await new Promise<void>((resolve) => {
      if (videoEl.readyState >= 3 && !videoEl.paused) return resolve();
      const done = () => {
        videoEl.removeEventListener("playing", done);
        clearTimeout(cap);
        resolve();
      };
      const cap = window.setTimeout(done, 15000); // never hang the demo on a stuck stream
      videoEl.addEventListener("playing", done);
    });
  }

  async speak(text: string): Promise<void> {
    if (!this.client) throw new Error("not connected");
    this.emit("speakingStart");
    const done = new Promise<void>((resolve) => (this.speakingEndResolver = resolve));
    // safety net if the persona-finished event never arrives
    clearTimeout(this.safetyTimer);
    this.safetyTimer = window.setTimeout(() => this.finishSpeaking(), (text.split(" ").length / 2.0) * 1000 + 4000);
    await this.client.talk(text);
    await done;
  }

  disconnect(): void {
    this.client?.stopStreaming();
    this.client = null;
  }

  private finishSpeaking(): void {
    clearTimeout(this.safetyTimer);
    this.speakingEndResolver?.();
    this.speakingEndResolver = null;
    this.emit("speakingEnd");
  }
}
