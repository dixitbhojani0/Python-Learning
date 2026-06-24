import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { environment } from '../../../environments/environment';
import { AdminService } from '../../core/services/admin.service';
import { SessionTurn } from '../../core/models/api.models';

@Component({
  selector: 'app-sessions',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatTableModule, MatButtonModule, MatInputModule,
    MatProgressSpinnerModule, MatIconModule,
  ],
  templateUrl: './sessions.html',
})
export class Sessions implements OnInit {
  turns   = signal<SessionTurn[]>([]);
  loading = signal(false);
  displayedColumns = ['created_at', 'user_role', 'project_id', 'query', 'response'];
  project = environment.defaultProject;

  constructor(private admin: AdminService) {}

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading.set(true);
    this.admin.getSessions(this.project).subscribe({
      next:  (res) => { this.turns.set(res.turns); this.loading.set(false); },
      error: ()    => { this.loading.set(false); },
    });
  }
}
