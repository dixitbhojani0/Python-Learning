import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatIconModule } from '@angular/material/icon';
import { environment } from '../../../environments/environment';
import { AdminService } from '../../core/services/admin.service';
import { ChunkItem } from '../../core/models/api.models';

@Component({
  selector: 'app-rag-manager',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatTableModule, MatButtonModule, MatInputModule, MatSelectModule,
    MatProgressSpinnerModule, MatChipsModule, MatSnackBarModule, MatIconModule,
  ],
  templateUrl: './rag-manager.html',
})
export class RagManager implements OnInit {
  chunks    = signal<ChunkItem[]>([]);
  loading   = signal(false);
  ingesting           = signal(false);
  ingestingConfluence = signal(false);
  ingestingJira       = signal(false);
  clearing            = signal(false);
  displayedColumns = ['source', 'type', 'doc_title', 'text_preview'];
  project = environment.defaultProject;
  docType = '';

  constructor(private admin: AdminService, private snack: MatSnackBar) {}

  ngOnInit(): void { this.loadChunks(); }

  loadChunks(): void {
    this.loading.set(true);
    this.admin.getChunks(this.project, this.docType).subscribe({
      next:  (res) => { this.chunks.set(res.chunks); this.loading.set(false); },
      error: ()    => { this.loading.set(false); this.snack.open('Failed to load chunks', 'OK', { duration: 3000 }); },
    });
  }

  triggerIngest(): void {
    this.ingesting.set(true);
    this.admin.triggerIngest({ project: this.project, use_llm: false, directory: '' }).subscribe({
      next: (res) => {
        this.ingesting.set(false);
        this.snack.open(`${res.chunks_ingested} chunks ingested in ${res.duration_seconds.toFixed(1)}s`, 'OK', { duration: 4000 });
        this.loadChunks();
      },
      error: () => { this.ingesting.set(false); this.snack.open('Ingest failed', 'OK', { duration: 3000 }); },
    });
  }

  ingestFromConfluence(): void {
    this.ingestingConfluence.set(true);
    this.admin.ingestFromConfluence(this.project, 'SDLC').subscribe({
      next: (res) => {
        this.ingestingConfluence.set(false);
        this.snack.open(
          `Confluence: ${res.pages_fetched} pages → ${res.chunks_ingested} chunks in ${res.duration_seconds.toFixed(1)}s`,
          'OK', { duration: 5000 }
        );
        this.loadChunks();
      },
      error: () => {
        this.ingestingConfluence.set(false);
        this.snack.open('Confluence ingest failed', 'OK', { duration: 3000 });
      },
    });
  }

  ingestFromJira(): void {
    this.ingestingJira.set(true);
    this.admin.ingestFromJira(this.project).subscribe({
      next: (res) => {
        this.ingestingJira.set(false);
        this.snack.open(
          `Jira: ${res.tickets_fetched} tickets → ${res.chunks_ingested} chunks in ${res.duration_seconds.toFixed(1)}s`,
          'OK', { duration: 5000 }
        );
        this.loadChunks();
      },
      error: () => {
        this.ingestingJira.set(false);
        this.snack.open('Jira ingest failed', 'OK', { duration: 3000 });
      },
    });
  }

  clearAll(): void {
    if (!confirm(`Clear ALL data for project "${this.project}"?\n\nThis deletes:\n- All RAG chunks\n- Semantic memory facts\n- Redis cache\n- Session history\n\nSource documents (data/ folder) are NOT deleted.`)) return;
    this.clearing.set(true);
    this.admin.clearAll(this.project).subscribe({
      next: (res) => {
        this.clearing.set(false);
        const r = res.results;
        this.snack.open(
          `Reset complete — chunks:${r.qdrant_rag_chunks} memory:${r.semantic_memory} cache:${r.redis_cache} sessions:${r.session_turns}`,
          'OK', { duration: 6000 }
        );
        this.loadChunks();
      },
      error: () => { this.clearing.set(false); this.snack.open('Clear failed', 'OK', { duration: 3000 }); },
    });
  }
}
