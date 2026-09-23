import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { ChatComponent } from './chat';
import { ChatEvent, ChatService } from '../../core/chat/chat.service';

const LANGS = {
  en: {
    chat: {
      title: 'Chat', placeholder: 'Message…', send: 'Send', sending: 'Sending…',
      emptyState: 'Send a message to start.', you: 'You', assistant: 'Assistant',
      error: 'The assistant could not respond.', logout: 'Sign out', sources: 'Sources: {{count}}',
      approvalRequired: 'This action requires approval.',
    },
  },
};

async function* genFrom(events: ChatEvent[]): AsyncGenerator<ChatEvent> {
  for (const e of events) yield e;
}

describe('ChatComponent', () => {
  let chatStub: { sendMessage: ReturnType<typeof vi.fn> };

  async function setup() {
    chatStub = { sendMessage: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [
        ChatComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: ChatService, useValue: chatStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ChatComponent);
    fixture.detectChanges();
    return fixture;
  }

  function setDraftAndSend(fixture: ReturnType<typeof TestBed.createComponent<ChatComponent>>, text: string) {
    const component = fixture.componentInstance as unknown as { draft: string; send: () => Promise<void> };
    component.draft = text;
    return component.send();
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('shows the empty state before any message is sent', async () => {
    const fixture = await setup();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="empty-state"]')).toBeTruthy();
  });

  it('appends the user message immediately and streams tokens into the assistant reply', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(
      genFrom([{ type: 'token', text: 'hel' }, { type: 'token', text: 'lo' }, { type: 'done', conversation_id: 'c1' }])
    );

    await setDraftAndSend(fixture, 'hi there');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="message-0"]')!.textContent).toContain('hi there');
    expect(el.querySelector('[data-testid="message-1"]')!.textContent).toContain('hello');
  });

  it('shows a sources count under the assistant message when a citations event arrives', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(
      genFrom([
        { type: 'citations', chunks: [{ chunk_id: 'c1', document_id: 'd1' }, { chunk_id: 'c2', document_id: 'd1' }] },
        { type: 'token', text: 'answer' },
        { type: 'done', conversation_id: 'c1' },
      ])
    );

    await setDraftAndSend(fixture, 'what is the wifi password?');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="citations-1"]')!.textContent).toContain('Sources: 2');
  });

  it('shows no sources line when no citations event arrives', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(genFrom([{ type: 'token', text: 'hi' }, { type: 'done', conversation_id: 'c1' }]));

    await setDraftAndSend(fixture, 'hello');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="citations-1"]')).toBeNull();
  });

  it('shows the tool result text under the assistant message when a tool_result event arrives', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(
      genFrom([
        { type: 'tool_result', tool: 'calculator', text: "Tool 'calculator' result: {\"result\": 4}" },
        { type: 'token', text: 'The answer is 4.' },
        { type: 'done', conversation_id: 'c1' },
      ])
    );

    await setDraftAndSend(fixture, 'calculate 2 + 2');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="tool-result-1"]')!.textContent).toContain('result": 4');
  });

  it('shows an approval-required notice and no token content when that event arrives', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(
      genFrom([
        { type: 'approval_required', approval_id: 'a1', tool: 'delete_all_documents', args: {} },
        { type: 'done', conversation_id: 'c1' },
      ])
    );

    await setDraftAndSend(fixture, 'delete all my documents');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="approval-required-1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="message-1"]')!.textContent?.trim()).toBe('Assistant:'); // no token content arrived
  });

  it('shows a typing indicator on the empty assistant placeholder before the first token arrives', async () => {
    const fixture = await setup();
    let resolveStream!: () => void;
    chatStub.sendMessage.mockReturnValue(
      (async function* () {
        await new Promise<void>((resolve) => (resolveStream = resolve));
        yield { type: 'done', conversation_id: 'c1' } as ChatEvent;
      })()
    );

    const sendPromise = setDraftAndSend(fixture, 'hi there');
    await Promise.resolve(); // let the async generator start and the placeholder render
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.typing-indicator')).toBeTruthy();

    resolveStream();
    await sendPromise;
  });

  it('replaces the typing indicator with real content once a token arrives', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(genFrom([{ type: 'token', text: 'hi' }, { type: 'done', conversation_id: 'c1' }]));

    await setDraftAndSend(fixture, 'hello');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.typing-indicator')).toBeNull();
  });

  it('renders a timestamp for every message', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(genFrom([{ type: 'token', text: 'hi' }, { type: 'done', conversation_id: 'c1' }]));

    await setDraftAndSend(fixture, 'hello');
    fixture.detectChanges();

    const times = (fixture.nativeElement as HTMLElement).querySelectorAll('.message-time');
    expect(times.length).toBe(2); // user message + assistant reply
    expect(times[0].textContent?.trim()).not.toBe('');
  });

  it('passes the conversation_id from a prior "done" event into the next send() call', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(genFrom([{ type: 'done', conversation_id: 'conv-42' }]));
    await setDraftAndSend(fixture, 'first');

    chatStub.sendMessage.mockReturnValue(genFrom([{ type: 'done', conversation_id: 'conv-42' }]));
    await setDraftAndSend(fixture, 'second');

    expect(chatStub.sendMessage).toHaveBeenNthCalledWith(2, 'second', 'conv-42');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows a translated error and removes the empty assistant placeholder when the stream fails', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(
      (async function* () {
        throw new Error('network down');
      })()
    );

    await setDraftAndSend(fixture, 'hi');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="chat-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="message-1"]')).toBeNull(); // placeholder removed, only the user message remains
  });

  it('shows a translated error and removes the placeholder when the backend sends an error event (e.g. a misconfigured provider)', async () => {
    const fixture = await setup();
    chatStub.sendMessage.mockReturnValue(
      genFrom([{ type: 'error', message: 'chat.error' }, { type: 'done', conversation_id: 'c1' }])
    );

    await setDraftAndSend(fixture, 'hi');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="chat-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="message-1"]')).toBeNull();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('does nothing when sending an empty/whitespace-only draft', async () => {
    const fixture = await setup();
    await setDraftAndSend(fixture, '   ');

    expect(chatStub.sendMessage).not.toHaveBeenCalled();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="empty-state"]')).toBeTruthy();
  });
});
