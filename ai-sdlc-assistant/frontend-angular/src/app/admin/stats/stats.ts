import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { AdminService } from '../../core/services/admin.service';
import { StatsResponse } from '../../core/models/api.models';

@Component({
  selector: 'app-stats',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatProgressSpinnerModule, MatButtonModule, MatIconModule],
  templateUrl: './stats.html',
})
export class Stats implements OnInit {
  stats  = signal<StatsResponse | null>(null);
  loading = signal(true);
  error   = signal('');

  constructor(private admin: AdminService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set('');
    this.admin.getStats().subscribe({
      next:  (s) => { this.stats.set(s); this.loading.set(false); },
      error: ()  => { this.error.set('Failed to load stats — is the backend running?'); this.loading.set(false); },
    });
  }
}
