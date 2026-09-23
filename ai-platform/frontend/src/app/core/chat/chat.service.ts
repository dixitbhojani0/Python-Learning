import { Injectable, inject } from '@angular/core';
import { AuthService } from '../auth/auth.service';

export interface Citation {
  chunk_id: string;
  document_id: string;
}

export type ChatEvent =
  | { type: 'token'; text: string }
  | { type: 'citations'; chunks: Citation[] }
  | { type: 'tool_result'; tool: string; text: string }
  | { type: 'approval_required'; approval_id: string; tool: string; args: Record<string, unknown> }
  | { type: 'error'; message: string }
  | { type: 'done'; conversation_id: string };

export class ChatRequestError extends Error {
  constructor(readonly status: number) {
    super(`Chat request failed with status ${status}`);
  }
}

/**
 * Raw fetch(), not HttpClient: EventSource only supports GET (no request
 * body, no custom headers), and our streaming endpoint is POST with a bearer
 * token — so this is the standard pattern for POST-based SSE consumption in
 * the browser, not a workaround.
 */
@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly auth = inject(AuthService);

  async *sendMessage(content: string, conversationId: string | null): AsyncGenerator<ChatEvent> {
    const response = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.auth.token() ?? ''}`,
      },
      body: JSON.stringify({ content, ...(conversationId ? { conversation_id: conversationId } : {}) }),
    });

    if (!response.ok || !response.body) {
      throw new ChatRequestError(response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() ?? ''; // last chunk may be a partial event — keep it for the next read

      for (const raw of events) {
        if (raw.startsWith('data: ')) {
          yield JSON.parse(raw.slice('data: '.length)) as ChatEvent;
        }
      }
    }
  }
}
