import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AuthService } from '../../core/auth/auth.service';
import { ChatService } from '../../core/chat/chat.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  citationCount?: number;
  toolResultText?: string;
  approvalRequired?: boolean;
}

@Component({
  selector: 'app-chat',
  imports: [FormsModule, TranslocoPipe, RouterLink],
  templateUrl: './chat.html',
})
export class ChatComponent {
  private readonly chatService = inject(ChatService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected draft = '';
  protected readonly messages = signal<ChatMessage[]>([]);
  protected readonly sending = signal(false);
  protected readonly errorMessage = signal<string | null>(null);
  private conversationId: string | null = null;

  protected async send(): Promise<void> {
    const content = this.draft.trim();
    if (!content || this.sending()) {
      return;
    }

    this.draft = '';
    this.errorMessage.set(null);
    this.sending.set(true);
    this.messages.update((current) => [...current, { role: 'user', content }, { role: 'assistant', content: '' }]);

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

  protected logout(): void {
    this.auth.logout();
    this.router.navigateByUrl('/login');
  }
}
