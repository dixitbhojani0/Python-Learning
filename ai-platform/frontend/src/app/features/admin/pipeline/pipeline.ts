import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, IngestionJobSummary } from '../../../core/admin/admin.service';

const KNOWN_STAGES = ['queued', 'chunking', 'embedding', 'storing', 'complete', 'failed'] as const;

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

  constructor() {
    this.loadJobs();
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
