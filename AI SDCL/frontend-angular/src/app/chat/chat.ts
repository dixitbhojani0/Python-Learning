import { Component, ViewChild, ElementRef, AfterViewChecked, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ChatService } from '../core/services/chat.service';
import { AuthService } from '../core/services/auth.service';
import { HitlCard } from './hitl-card/hitl-card';
import { ChatMessage } from '../core/models/api.models';
import { MarkdownPipe } from '../core/pipes/markdown.pipe';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatInputModule, MatButtonModule, MatProgressSpinnerModule,
    MatChipsModule, MatTooltipModule,
    HitlCard, MarkdownPipe,
  ],
  templateUrl: './chat.html',
  styleUrl: './chat.css',
})
export class Chat implements AfterViewChecked {
  @ViewChild('messageList') private messageList!: ElementRef;

  messages    = signal<ChatMessage[]>([]);
  inputText   = signal('');
  loading     = signal(false);
  sessionId   = '';

  pendingHitl = computed(() =>
    this.messages().some(m => m.hitlRequired && !m.hitlResolved)
  );

  constructor(private chatSvc: ChatService, public auth: AuthService) {}

  ngAfterViewChecked(): void {
    this.scrollToBottom();
  }

  private scrollToBottom(): void {
    try {
      this.messageList.nativeElement.scrollTop = this.messageList.nativeElement.scrollHeight;
    } catch {}
  }

  send(): void {
    const text = this.inputText().trim();
    if (!text || this.loading() || this.pendingHitl()) return;

    this.messages.update(msgs => [...msgs, { role: 'user', text }]);
    this.inputText.set('');
    this.loading.set(true);

    const session = this.auth.getSession()!;
    this.chatSvc
      .sendMessage({
        message: text,
        project: session.project,
        session_id: this.sessionId || undefined,
      })
      .subscribe({
        next: (res) => {
          if (!this.sessionId) this.sessionId = res.session_id;
          this.messages.update(msgs => [
            ...msgs,
            {
              role: 'assistant',
              text: res.response,
              sources: res.sources,
              confidence: res.confidence,
              cached: res.response_cached,
              hitlRequired: res.hitl_required,
              hitlActionId: res.hitl_action_id,
            },
          ]);
          this.loading.set(false);
        },
        error: () => {
          this.messages.update(msgs => [
            ...msgs,
            { role: 'assistant', text: 'Something went wrong. Please try again.' },
          ]);
          this.loading.set(false);
        },
      });
  }

  onHitlResolved(msg: ChatMessage, result: string): void {
    this.messages.update(msgs =>
      msgs.map(m => (m === msg ? { ...m, hitlResolved: true } : m))
    );
    this.messages.update(msgs => [...msgs, { role: 'assistant', text: result }]);
  }

  onEnter(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  formatConfidence(value: number): string {
    return (value * 100).toFixed(0) + '%';
  }
}
