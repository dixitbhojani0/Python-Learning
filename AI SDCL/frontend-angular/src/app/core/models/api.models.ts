// TypeScript mirrors of backend/api/models/schemas.py Pydantic models

// ── Auth (Angular-side only)
export interface UserSession {
  token: string;
  role: 'developer' | 'manager' | 'stakeholder' | 'technical_leader' | 'admin';
  name: string;
  project: string;
}

// ── Chat
export interface ChatRequest {
  message: string;
  project: string;
  session_id?: string;
}

export interface ChatResponse {
  response: string;
  confidence: number;
  sources: string[];
  session_id: string;
  strategy: string;
  hitl_required: boolean;
  hitl_action_id: string | null;
  response_cached: boolean;
}

// ── HITL
export interface HITLRequest {
  hitl_id: string;
}

export interface HITLResponse {
  response: string;
  decision: 'approved' | 'rejected';
  hitl_id: string;
}

// ── Admin
export interface StatsResponse {
  qdrant_chunks: number;
  qdrant_collection: string;
  redis_keys: number;
  session_turns: number;
  semantic_facts: number;
  app_env: string;
  default_project: string;
}

export interface SemanticFact {
  id: string;
  text: string;
  category: string;
  project_id: string;
  created_at: string;
  source_query: string;
}

export interface ClearResult {
  status: string;
  project: string;
  results: {
    qdrant_rag_chunks: string;
    semantic_memory: string;
    redis_cache: string;
    session_turns: string;
  };
}

export interface ChunkItem {
  id: string;
  project: string;
  type: string;
  source: string;
  doc_title: string;
  stale: boolean;
  text_preview: string;
  has_parent: boolean;
  has_context: boolean;
}

export interface IngestRequest {
  project: string;
  use_llm: boolean;
  directory: string;
}

export interface IngestResponse {
  chunks_ingested: number;
  duration_seconds: number;
  message: string;
}

export interface SessionTurn {
  query: string;
  response: string;
  user_role: string;
  project_id: string;
  created_at: string;
}

// ── Chat message (UI-side only, not an API schema)
export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  sources?: string[];
  confidence?: number;
  cached?: boolean;
  hitlRequired?: boolean;
  hitlActionId?: string | null;
  hitlResolved?: boolean;
}
