/**
 * The provider contract. The scene engine and all UI talk ONLY to this interface —
 * swapping avatar vendors never touches them.
 *
 * Implementations:
 * - AnamProvider (now, free tier): Anam JS SDK over WebRTC; ASR built in.
 * - DryRunProvider (rehearsal): browser speechSynthesis, no connection, no cost.
 * - AzureProvider (later, paid): ICE token from backend → RTCPeerConnection +
 *   AvatarSynthesizer.startAvatarAsync; speak() = speakSsmlAsync (gains SSML
 *   styles/rate); userSpeech from Azure SpeechRecognizer.
 * - UnrealAceProvider (later, premium): connect() attaches a pixel-streaming
 *   WebRTC feed from a GPU host running MetaHuman + Audio2Face; speak() POSTs
 *   text to the ACE orchestrator via a backend router.
 */
export interface ProviderEvents {
  ready: () => void;
  /** Advisor's recognized speech (only if capabilities.listens). */
  userSpeech: (transcript: string) => void;
  speakingStart: () => void;
  speakingEnd: () => void;
  disconnected: () => void;
  error: (message: string) => void;
}

import type { SessionInfo } from "../types";

export interface AvatarProvider {
  /** What the UI can rely on. DryRun: { video: false, listens: false }. */
  readonly capabilities: { video: boolean; listens: boolean };
  /** Establish the media session and attach video/audio to the element. `session` comes from POST /api/v1/session. */
  connect(videoEl: HTMLVideoElement, session?: SessionInfo): Promise<void>;
  /** Speak exact text with lip sync. Resolves when speech has finished. */
  speak(text: string): Promise<void>;
  disconnect(): void;
  on<E extends keyof ProviderEvents>(event: E, cb: ProviderEvents[E]): void;
}

/** Minimal typed emitter shared by provider implementations. */
export class ProviderEventEmitter {
  private listeners = new Map<keyof ProviderEvents, Array<(...args: never[]) => void>>();

  on<E extends keyof ProviderEvents>(event: E, cb: ProviderEvents[E]): void {
    const list = this.listeners.get(event) ?? [];
    list.push(cb as (...args: never[]) => void);
    this.listeners.set(event, list);
  }

  protected emit<E extends keyof ProviderEvents>(event: E, ...args: Parameters<ProviderEvents[E]>): void {
    for (const cb of this.listeners.get(event) ?? []) (cb as (...a: unknown[]) => void)(...args);
  }
}
