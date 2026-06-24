import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../environments/environment';
import { AdminService } from '../../core/services/admin.service';
import { SemanticFact } from '../../core/models/api.models';

@Component({
  selector: 'app-memory',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatTableModule, MatButtonModule, MatInputModule,
    MatChipsModule, MatIconModule, MatSnackBarModule,
    MatProgressSpinnerModule, MatTooltipModule,
  ],
  templateUrl: './memory.html',
})
export class MemoryComponent implements OnInit {
  facts    = signal<SemanticFact[]>([]);
  loading  = signal(false);
  clearing = signal(false);
  project  = environment.defaultProject;
  displayedColumns = ['category', 'text', 'source_query', 'created_at'];

  constructor(private admin: AdminService, private snack: MatSnackBar) {}

  ngOnInit(): void { this.loadFacts(); }

  loadFacts(): void {
    this.loading.set(true);
    this.admin.getMemoryFacts(this.project).subscribe({
      next:  (res) => { this.facts.set(res.facts); this.loading.set(false); },
      error: ()    => { this.loading.set(false); this.snack.open('Failed to load memory facts', 'OK', { duration: 3000 }); },
    });
  }

  clearMemory(): void {
    if (!confirm(`Delete all semantic memory facts for project "${this.project}"?`)) return;
    this.clearing.set(true);
    this.admin.clearMemory(this.project).subscribe({
      next: () => {
        this.clearing.set(false);
        this.facts.set([]);
        this.snack.open('Semantic memory cleared', 'OK', { duration: 3000 });
      },
      error: () => { this.clearing.set(false); this.snack.open('Clear failed', 'OK', { duration: 3000 }); },
    });
  }

  categoryColor(cat: string): string {
    const map: Record<string, string> = {
      blocker: '#f44336', team_assignment: '#2196f3', sprint_status: '#ff9800',
      decision: '#9c27b0', resolution: '#4caf50',
    };
    return map[cat] ?? '#607d8b';
  }
}
