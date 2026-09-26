import { TestBed } from '@angular/core/testing';

import { Chat } from './chat';
import { ChatService } from '../core/services/chat.service';
import { AuthService } from '../core/services/auth.service';
import { ChatMessage } from '../core/models/api.models';

// E9 — "why this answer?" decision trace panel. The backend omits `trace`
// entirely when there's nothing to explain (blocked query, HITL proposal,
// no-evidence refusal) — the panel must not render in that case, and must
// render each section only when that section has data.
describe('Chat — decision trace panel (E9)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Chat],
      providers: [
        { provide: ChatService, useValue: {} },
        {
          provide: AuthService,
          useValue: { getSession: () => ({ name: 'Test', role: 'developer', token: 't', project: 'SDLC' }) },
        },
      ],
    }).compileComponents();
  });

  function render(message: ChatMessage): HTMLElement {
    const fixture = TestBed.createComponent(Chat);
    fixture.componentInstance.messages.set([message]);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders no trace panel when the message has no trace', () => {
    const el = render({ role: 'assistant', text: 'answer', agent: 'cross_source' });
    expect(el.querySelector('details.trace')).toBeNull();
  });

  it('renders no trace panel on user messages even if trace were present', () => {
    const el = render({
      role: 'user',
      text: 'question',
      trace: { routing_reason: 'x', tools_called: [], top_chunks: [] },
    });
    expect(el.querySelector('details.trace')).toBeNull();
  });

  it('renders routing reason, tools called, and top chunks when all are present', () => {
    const el = render({
      role: 'assistant',
      text: 'answer',
      agent: 'cross_source',
      trace: {
        routing_reason: 'chains Jira + GitHub tools for a cross-system question',
        tools_called: [{ tool: 'jira_get_blocked_tickets', ok: true }],
        top_chunks: [{ source: 'local:Sprint Notes', score: 0.74 }],
      },
    });

    const details = el.querySelector('details.trace');
    expect(details).not.toBeNull();
    expect(details!.textContent).toContain('Why this answer?');
    expect(details!.textContent).toContain('chains Jira + GitHub tools for a cross-system question');
    expect(details!.textContent).toContain('jira_get_blocked_tickets');
    expect(details!.textContent).toContain('local:Sprint Notes');
    expect(details!.textContent).toContain('0.74');
  });

  it('omits the "Tools called" section when no tools ran', () => {
    const el = render({
      role: 'assistant',
      text: 'answer',
      trace: {
        routing_reason: 'document lookup, no live-system reference',
        tools_called: [],
        top_chunks: [{ source: 'local:Checklist', score: 0.81 }],
      },
    });

    const details = el.querySelector('details.trace')!;
    expect(details.textContent).not.toContain('Tools called');
    expect(details.textContent).toContain('Top chunks used');
  });

  it('marks a failed tool call distinctly from a successful one', () => {
    const el = render({
      role: 'assistant',
      text: 'answer',
      trace: {
        routing_reason: '',
        tools_called: [
          { tool: 'jira_get_ticket', ok: true },
          { tool: 'github_list_open_prs', ok: false },
        ],
        top_chunks: [],
      },
    });

    const details = el.querySelector('details.trace')!;
    expect(details.querySelector('li.tool-ok')?.textContent).toContain('jira_get_ticket');
    expect(details.querySelector('li.tool-err')?.textContent).toContain('github_list_open_prs');
  });
});
