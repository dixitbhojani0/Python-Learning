import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { vi } from 'vitest';
import { ChatService } from './chat.service';
import { AuthService } from '../auth/auth.service';

function fakeSseResponse(chunks: string[], opts: { ok?: boolean; status?: number } = {}) {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: opts.ok ?? true,
    status: opts.status ?? 200,
    body:
      opts.ok === false
        ? null
        : {
            getReader: () => ({
              read: async () => {
                if (i < chunks.length) {
                  const value = encoder.encode(chunks[i]);
                  i += 1;
                  return { done: false, value };
                }
                return { done: true, value: undefined };
              },
            }),
          },
  };
}

async function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of gen) out.push(item);
  return out;
}

describe('ChatService', () => {
  let service: ChatService;
  let auth: AuthService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(ChatService);
    auth = TestBed.inject(AuthService);
    vi.restoreAllMocks();
  });

  // ── Positive ────────────────────────────────────────────────────────────

  it('parses token and done events from the SSE stream', async () => {
    const sse =
      'data: {"type":"token","text":"hel"}\n\n' +
      'data: {"type":"token","text":"lo"}\n\n' +
      'data: {"type":"done","conversation_id":"abc-123"}\n\n';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse([sse]) as unknown as Response);

    const events = await collect(service.sendMessage('hi', null));

    expect(events).toEqual([
      { type: 'token', text: 'hel' },
      { type: 'token', text: 'lo' },
      { type: 'done', conversation_id: 'abc-123' },
    ]);
  });

  it('parses a citations event alongside token/done events', async () => {
    const sse =
      'data: {"type":"citations","chunks":[{"chunk_id":"c1","document_id":"d1"}]}\n\n' +
      'data: {"type":"token","text":"hi"}\n\n' +
      'data: {"type":"done","conversation_id":"c-1"}\n\n';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse([sse]) as unknown as Response);

    const events = await collect(service.sendMessage('hi', null));

    expect(events[0]).toEqual({ type: 'citations', chunks: [{ chunk_id: 'c1', document_id: 'd1' }] });
  });

  it('parses a tool_result event', async () => {
    const sse = 'data: {"type":"tool_result","tool":"calculator","text":"Tool \'calculator\' result: {\\"result\\": 4}"}\n\n';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse([sse]) as unknown as Response);

    const events = await collect(service.sendMessage('calculate 2 + 2', null));

    expect(events[0]).toEqual({ type: 'tool_result', tool: 'calculator', text: "Tool 'calculator' result: {\"result\": 4}" });
  });

  it('parses an approval_required event', async () => {
    const sse = 'data: {"type":"approval_required","approval_id":"a1","tool":"delete_all_documents","args":{}}\n\n';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse([sse]) as unknown as Response);

    const events = await collect(service.sendMessage('delete all my documents', null));

    expect(events[0]).toEqual({ type: 'approval_required', approval_id: 'a1', tool: 'delete_all_documents', args: {} });
  });

  it('handles an SSE event split across two stream chunks', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      fakeSseResponse(['data: {"type":"token","tex', 't":"hi"}\n\n']) as unknown as Response
    );

    const events = await collect(service.sendMessage('hi', null));

    expect(events).toEqual([{ type: 'token', text: 'hi' }]);
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('throws ChatRequestError on a non-OK response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse([], { ok: false, status: 403 }) as unknown as Response);

    await expect(collect(service.sendMessage('hi', null))).rejects.toMatchObject({ status: 403 });
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('sends the bearer token from AuthService, not a hardcoded value', async () => {
    sessionStorage.setItem('ai-platform.access_token', 'the-real-token');
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(ChatService);
    auth = TestBed.inject(AuthService);
    expect(auth.token()).toBe('the-real-token');

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse(['']) as unknown as Response);
    await collect(service.sendMessage('hi', null));

    const [, init] = fetchSpy.mock.calls[0];
    expect((init as RequestInit).headers).toMatchObject({ Authorization: 'Bearer the-real-token' });
    sessionStorage.clear();
  });

  it('includes conversation_id in the request body only when continuing a conversation', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeSseResponse(['']) as unknown as Response);

    await collect(service.sendMessage('hi', 'conv-1'));

    const [, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toEqual({ content: 'hi', conversation_id: 'conv-1' });
  });
});
