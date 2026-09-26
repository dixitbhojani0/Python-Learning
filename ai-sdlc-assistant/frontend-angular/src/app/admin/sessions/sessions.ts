import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { environment } from '../../../environments/environment';
import { AdminService } from '../../core/services/admin.service';
import { SessionTurn } from '../../core/models/api.models';

@Component({
  selector: 'app-sessions',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatTableModule, MatButtonModule, MatInputModule,
    MatProgressSpinnerModule, MatIconModule, MatSnackBarModule,
  ],
  templateUrl: './sessions.html',
})
export class Sessions implements OnInit {
  turns   = signal<SessionTurn[]>([]);
  loading = signal(false);
  displayedColumns = ['created_at', 'user_role', 'project_id', 'query', 'response'];
  project = environment.defaultProject;

  constructor(private admin: AdminService, private snack: MatSnackBar) {}

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading.set(true);
    this.admin.getSessions(this.project).subscribe({
      next:  (res) => { this.turns.set(res.turns); this.loading.set(false); },
      error: (err) => {
        this.loading.set(false);
        this.snack.open(err?.error?.detail || 'Failed to load sessions', 'OK', { duration: 4000 });
      },
    });
  }
}
