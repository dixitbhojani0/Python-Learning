import { Component, Input, Output, EventEmitter, OnInit, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { CommonModule } from '@angular/common';
import { HitlService } from '../../core/services/hitl.service';

@Component({
  selector: 'app-hitl-card',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatCardModule, MatProgressSpinnerModule],
  templateUrl: './hitl-card.html',
})
export class HitlCard implements OnInit {
  @Input() hitlId!: string;
  @Input() actionType: string | null = null;
  @Output() resolved = new EventEmitter<string>();

  busy  = signal(false);
  error = signal('');

  constructor(private hitl: HitlService) {}

  ngOnInit(): void {
    // E8 — this action type was already "approve all"-ed earlier in this
    // conversation: resolve immediately, no card shown at all.
    if (this.actionType && this.hitl.isAutoApproved(this.actionType)) {
      this.busy.set(true);
      this.hitl.approve(this.hitlId).subscribe({
        next:  (res) => this.resolved.emit(`_(Auto-approved for this conversation)_\n\n${res.response}`),
        error: (err) => this.onActionError(err),
      });
    }
  }

  get canApproveAll(): boolean {
    return !!this.actionType && this.hitl.isApproveAllEligible(this.actionType);
  }

  approve(): void {
    this.busy.set(true);
    this.error.set('');
    this.hitl.approve(this.hitlId).subscribe({
      next:  (res) => this.resolved.emit(res.response),
      error: (err) => this.onActionError(err),
    });
  }

  approveAll(): void {
    this.busy.set(true);
    this.error.set('');
    this.hitl.approve(this.hitlId, true).subscribe({
      next:  (res) => {
        if (this.actionType) this.hitl.markAutoApproved(this.actionType);
        this.resolved.emit(res.response);
      },
      error: (err) => this.onActionError(err),
    });
  }

  reject(): void {
    this.busy.set(true);
    this.error.set('');
    this.hitl.reject(this.hitlId).subscribe({
      next:  (res) => this.resolved.emit(res.response),
      error: (err) => this.onActionError(err),
    });
  }

  /**
   * Surface the backend's explanation (FastAPI returns it as `detail`) inline
   * and keep the card visible — a blocked Approve (409) must still let the user
   * Reject. A 404 means the action is already gone, so close the card.
   */
  private onActionError(err: HttpErrorResponse): void {
    this.busy.set(false);
    const detail = err?.error?.detail;
    if (err?.status === 404) {
      this.resolved.emit(detail || 'This action was already resolved or has expired.');
      return;
    }
    this.error.set(detail || 'Something went wrong. Please try again.');
  }
}
