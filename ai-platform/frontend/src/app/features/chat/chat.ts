import { Component, ElementRef, effect, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe } from '@jsverse/transloco';
import { ChatService } from '../../core/chat/chat.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  citationCount?: number;
  toolResultText?: string;
  approvalRequired?: boolean;
}

const timeFormatter = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });

@Component({
  selector: 'app-chat',
  imports: [FormsModule, TranslocoPipe],
  templateUrl: './chat.html',
})
export class ChatComponent {
  private readonly chatService = inject(ChatService);

  protected draft = '';
  protected readonly messages = signal<ChatMessage[]>([]);
  protected readonly sending = signal(false);
  protected readonly errorMessage = signal<string | null>(null);
  private conversationId: string | null = null;

  private readonly scrollAnchor = viewChild<ElementRef<HTMLElement>>('scrollAnchor');

  constructor() {
    // Auto-scroll to the newest message/token as it streams in — every chat
    // product does this; without it a long reply scrolls the transcript out
    // from under the reader. scrollIntoView is unavailable in the jsdom test
    // environment, hence the optional chaining rather than a hard call.
    effect(() => {
      this.messages();
      queueMicrotask(() => this.scrollAnchor()?.nativeElement.scrollIntoView?.({ block: 'end', behavior: 'smooth' }));
    });
  }

  protected formatTime(timestamp: number): string {
    return timeFormatter.format(timestamp);
  }

  protected isTypingPlaceholder(index: number): boolean {
    const list = this.messages();
    const message = list[index];
    return (
      this.sending() &&
      index === list.length - 1 &&
      message.role === 'assistant' &&
      message.content === '' &&
      !message.toolResultText &&
      !message.approvalRequired
    );
  }

  protected async send(): Promise<void> {
    const content = this.draft.trim();
    if (!content || this.sending()) {
      return;
    }

    this.draft = '';
    this.errorMessage.set(null);
    this.sending.set(true);
    this.messages.update((current) => [
      ...current,
      { role: 'user', content, timestamp: Date.now() },
      { role: 'assistant', content: '', timestamp: Date.now() },
    ]);

    try {
      for await (const event of this.chatService.sendMessage(content, this.conversationId)) {
        if (event.type === 'token') {
          this.appendToLastAssistantMessage(event.text);
        } else if (event.type === 'citations') {
          this.setCitationCountOnLastAssistantMessage(event.chunks.length);
        } else if (event.type === 'tool_result') {
          this.setToolResultOnLastAssistantMessage(event.text);
        } else if (event.type === 'approval_required') {
          this.setApprovalRequiredOnLastAssistantMessage();
        } else if (event.type === 'error') {
          this.errorMessage.set(event.message);
          this.messages.update((current) => current.slice(0, -1));
        } else {
          this.conversationId = event.conversation_id;
        }
      }
    } catch {
      this.errorMessage.set('chat.error');
      // Remove the empty/partial assistant placeholder — an error must not
      // leave a blank assistant bubble sitting in the transcript.
      this.messages.update((current) => current.slice(0, -1));
    } finally {
      this.sending.set(false);
    }
  }

  private appendToLastAssistantMessage(text: string): void {
    this.messages.update((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, content: last.content + text };
      return next;
    });
  }

  private setCitationCountOnLastAssistantMessage(count: number): void {
    this.messages.update((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, citationCount: count };
      return next;
    });
  }

  private setToolResultOnLastAssistantMessage(text: string): void {
    this.messages.update((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, toolResultText: text };
      return next;
    });
  }

  private setApprovalRequiredOnLastAssistantMessage(): void {
    this.messages.update((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, approvalRequired: true };
      return next;
    });
  }
}
