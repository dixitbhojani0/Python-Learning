import { DatePipe } from '@angular/common';
import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, IngestionJobSummary } from '../../../core/admin/admin.service';

const KNOWN_STAGES = ['queued', 'parsing', 'chunking', 'embedding', 'storing', 'complete', 'failed'] as const;

// Mirrors backend/app/rag/parsers — the real, complete set of file types
// anything in that registry can actually parse (not invented ahead of a
// real parser existing for it).
const SUPPORTED_FILE_TYPES = ['.txt', '.md', '.csv', '.pdf', '.docx'] as const;

@Component({
  selector: 'app-admin-pipeline',
  imports: [FormsModule, TranslocoPipe, RouterLink, DatePipe],
  templateUrl: './pipeline.html',
})
export class AdminPipelineComponent {
  private readonly admin = inject(AdminService);

  protected readonly knownStages = KNOWN_STAGES;
  protected selectedStage = '';
  protected readonly jobs = signal<IngestionJobSummary[]>([]);
  protected readonly jobsError = signal<string | null>(null);
  protected readonly loading = signal(false);

  protected ingestTitle = '';
  protected ingestContent = '';
  protected readonly ingesting = signal(false);
  protected readonly ingestError = signal<string | null>(null);

  protected readonly supportedFileTypes = SUPPORTED_FILE_TYPES;
  protected uploadTitle = '';
  protected readonly selectedFile = signal<File | null>(null);
  protected readonly uploading = signal(false);
  protected readonly uploadError = signal<string | null>(null);
  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  constructor() {
    this.loadJobs();
  }

  protected onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile.set(input.files?.[0] ?? null);
  }

  protected async upload(): Promise<void> {
    const file = this.selectedFile();
    if (!file || this.uploading()) {
      return;
    }

    this.uploading.set(true);
    this.uploadError.set(null);
    try {
      await this.admin.createIngestionJobFromFile(file, this.uploadTitle.trim() || null);
      this.uploadTitle = '';
      this.selectedFile.set(null);
      const input = this.fileInput();
      if (input) {
        input.nativeElement.value = ''; // native file inputs don't clear via data binding
      }
      await this.loadJobs();
    } catch {
      this.uploadError.set('admin.uploadFailed');
    } finally {
      this.uploading.set(false);
    }
  }

  protected async loadJobs(): Promise<void> {
    this.loading.set(true);
    this.jobsError.set(null);
    try {
      this.jobs.set(await this.admin.listIngestionJobs(this.selectedStage || null));
    } catch {
      this.jobsError.set('admin.accessDenied');
    } finally {
      this.loading.set(false);
    }
  }

  protected async ingest(): Promise<void> {
    const title = this.ingestTitle.trim();
    const content = this.ingestContent.trim();
    if (!title || !content || this.ingesting()) {
      return;
    }

    this.ingesting.set(true);
    this.ingestError.set(null);
    try {
      await this.admin.createIngestionJob(title, content);
      this.ingestTitle = '';
      this.ingestContent = '';
      await this.loadJobs();
    } catch {
      this.ingestError.set('admin.ingestFailed');
    } finally {
      this.ingesting.set(false);
    }
  }

  protected badgeClass(stage: string): string {
    switch (stage) {
      case 'complete':
        return 'badge-success';
      case 'failed':
        return 'badge-danger';
      case 'queued':
        return 'badge-warning';
      default:
        return 'badge-info';
    }
  }
}
