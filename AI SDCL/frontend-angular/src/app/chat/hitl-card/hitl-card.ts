import { Component, Input, Output, EventEmitter, signal } from '@angular/core';
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
export class HitlCard {
  @Input() hitlId!: string;
  @Output() resolved = new EventEmitter<string>();

  busy = signal(false);

  constructor(private hitl: HitlService) {}

  approve(): void {
    this.busy.set(true);
    this.hitl.approve(this.hitlId).subscribe({
      next:  (res) => this.resolved.emit(res.response),
      error: ()    => { this.busy.set(false); },
    });
  }

  reject(): void {
    this.busy.set(true);
    this.hitl.reject(this.hitlId).subscribe({
      next:  (res) => this.resolved.emit(res.response),
      error: ()    => { this.busy.set(false); },
    });
  }
}
