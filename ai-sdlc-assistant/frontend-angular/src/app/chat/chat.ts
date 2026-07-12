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
import { ChatMessage, ImageRef } from '../core/models/api.models';
import { MarkdownPipe } from '../core/pipes/markdown.pipe';
import { environment } from '../../environments/environment';

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
  private shouldScroll = false;

  pendingHitl = computed(() =>
    this.messages().some(m => m.hitlRequired && !m.hitlResolved)
  );

  constructor(private chatSvc: ChatService, public auth: AuthService) {}

  ngAfterViewChecked(): void {
    if (!this.shouldScroll) return;
    this.scrollToBottom();
    if (!this.loading()) this.shouldScroll = false;
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
    this.shouldScroll = true;

    // Generate stream_id client-side so SSE can open BEFORE POST returns.
    // If we waited for the HTTP response to get stream_id, the graph would
    // already be done and all tokens would be sitting in Redis unread.
    const streamId = crypto.randomUUID();

    // Add streaming placeholder — tokens will be appended to this message.
    this.messages.update(msgs => [...msgs, { role: 'assistant', text: '' }]);

    // Open SSE first, before the POST fires.
    const sseSubscription = this.chatSvc.streamResponse(streamId).subscribe({
      next: (ev) => {
        const data = JSON.parse(ev.data);
        if (data.type === 'token') {
          this.messages.update(msgs => {
            const updated = [...msgs];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              text: updated[updated.length - 1].text + data.text,
            };
            return updated;
          });
        }
        if (data.type === 'done') sseSubscription.unsubscribe();
      },
      error: () => sseSubscription.unsubscribe(),
    });

    const session = this.auth.getSession()!;
    this.chatSvc
      .sendMessage({
        message: text,
        project: session.project,
        session_id: this.sessionId || undefined,
        stream_id: streamId,
      })
      .subscribe({
        next: (res) => {
          if (!this.sessionId) this.sessionId = res.session_id;
          // res.response is always the authoritative final answer — the backend can
          // revise the streamed draft after it starts (persona rewrite, faithfulness
          // retry), so it must replace the streamed text, not just fill in if empty.
          this.messages.update(msgs => {
            const updated = [...msgs];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              text: res.response,
              sources: res.sources,
              confidence: res.confidence,
              cached: res.response_cached,
              agent: res.agent,
              strategy: res.strategy,
              relevancy: res.relevancy,
              faithfulness: res.faithfulness,
              hitlRequired: res.hitl_required,
              hitlActionId: res.hitl_action_id,
              images: res.images ?? [],
            };
            return updated;
          });
          this.loading.set(false);
        },
        error: () => {
          sseSubscription.unsubscribe();
          this.messages.update(msgs => {
            const updated = [...msgs];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              text: 'Something went wrong. Please try again.',
            };
            return updated;
          });
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

  /** Friendly name for the agent that handled the query. */
  agentLabel(agent?: string): string {
    const map: Record<string, string> = {
      cross_source: 'Cross-Source', risk: 'Risk', ticket: 'Ticket',
      pr_review: 'PR Review', release_readiness: 'Release', notify: 'Notify',
    };
    return agent ? (map[agent] ?? agent) : '';
  }

  /** Friendly label for the RAG strategy. Empty (hidden) for the unremarkable cases. */
  strategyLabel(strategy?: string): string {
    const map: Record<string, string> = {
      corrective: '🔄 corrective RAG', full_document: '📄 full document',
      degraded: '⚠️ low confidence',
    };
    return strategy ? (map[strategy] ?? '') : '';
  }

  /** Build an absolute image URL — backend returns paths relative to the API base. */
  imageUrl(img: ImageRef): string {
    const path = img.url || '';
    return /^https?:\/\//.test(path) ? path : `${environment.apiUrl}${path}`;
  }
}
