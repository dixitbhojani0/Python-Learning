export interface KeyValue {
  label: string;
  value: string;
}

/** Structured card data from GET /api/v1/script — rendered by CardRenderer, never raw HTML. */
export interface CardData {
  /** Icon name from the backend; mapped to an SVG in CardRenderer. */
  icon?: string;
  title: string;
  badge?: string;
  badgeTone?: "green" | "amber";
  keyValues?: KeyValue[];
  bullets?: string[];
  chips?: string[];
  note?: string;
}

/** One question→answer pair from backend/scenes.json (human-editable field names). */
export interface Scene {
  id: number;
  /** Present when fetched with ?all=1 (admin); the demo endpoint filters these out. */
  enabled?: boolean;
  topic: string;
  /** The advisor's question — shown as a speech bubble in autoplay. */
  question: string;
  /** Exactly what the avatar speaks. */
  answer: string;
  /** Lowercase words that route recognized advisor speech to this scene. */
  keywords: string[];
  searching?: boolean;
  closing?: boolean;
  card?: CardData;
}

export type Status = "idle" | "connecting" | "listening" | "thinking" | "speaking";

/** One document in the approved knowledge base registry (GET /api/v1/knowledge). */
export interface KnowledgeDoc {
  id: number;
  name: string;
  category: string;
  sizeKb: number;
  chunks: number;
  indexedAt: string;
  status: "indexed" | "processing";
}

export interface KnowledgeInfo {
  documents: KnowledgeDoc[];
  embeddingModel: string;
  totalChunks: number;
}

/** One entry in the conversation side panel. */
export interface ChatMessage {
  id: number;
  role: "you" | "assistant" | "system";
  text: string;
  time: string;
  card?: CardData;
}

export interface SessionInfo {
  provider: string;
  sessionToken?: string;
  [key: string]: unknown; // future providers add their own fields (iceServers, streamUrl, ...)
}
