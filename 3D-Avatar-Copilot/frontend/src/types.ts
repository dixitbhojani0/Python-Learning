export interface KeyValue {
  label: string;
  value: string;
}

/** Structured card data from GET /api/v1/script — rendered by CardRenderer, never raw HTML. */
export interface CardData {
  title: string;
  badge?: string;
  badgeTone?: "green" | "amber";
  keyValues?: KeyValue[];
  bullets?: string[];
  chips?: string[];
  note?: string;
}

export interface Scene {
  id: number;
  label: string;
  advisorLine: string;
  triggers: string[];
  spokenText: string;
  searching?: boolean;
  closing?: boolean;
  card?: CardData;
}

export type Status = "idle" | "connecting" | "listening" | "thinking" | "speaking";

export interface SessionInfo {
  provider: string;
  sessionToken?: string;
  [key: string]: unknown; // future providers add their own fields (iceServers, streamUrl, ...)
}
