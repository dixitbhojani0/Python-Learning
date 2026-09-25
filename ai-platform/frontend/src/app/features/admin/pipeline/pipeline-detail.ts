import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, IngestionJobDetail } from '../../../core/admin/admin.service';

// The real, complete stage order rag/pipeline.py ever assigns (see its own
// module docstring) — this IS the tracker, not a decorative approximation.
const STAGE_ORDER = ['queued', 'parsing', 'chunking', 'embedding', 'storing', 'complete'] as const;

export interface TrackerNode {
  key: string;
  status: 'done' | 'current' | 'pending' | 'failed';
}

@Component({
  selector: 'app-admin-pipeline-detail',
  imports: [TranslocoPipe, RouterLink, DatePipe],
  templateUrl: './pipeline-detail.html',
})
export class AdminPipelineDetailComponent {
  private readonly admin = inject(AdminService);
  private readonly route = inject(ActivatedRoute);

  protected readonly job = signal<IngestionJobDetail | null>(null);
  protected readonly error = signal<string | null>(null);

  protected readonly trackerNodes = computed<TrackerNode[]>(() => {
    const job = this.job();
    if (!job) {
      return [];
    }
    if (job.stage === 'complete') {
      return STAGE_ORDER.map((key) => ({ key, status: 'done' as const }));
    }
    if (job.stage === 'failed') {
      const failedAtStage = job.stage_log.length > 0 ? job.stage_log[job.stage_log.length - 1].stage : 'queued';
      const failedIndex = Math.max(0, STAGE_ORDER.indexOf(failedAtStage as (typeof STAGE_ORDER)[number]));
      return STAGE_ORDER.map((key, i) => ({
        key,
        status: i < failedIndex ? 'done' : i === failedIndex ? 'failed' : 'pending',
      }));
    }
    const currentIndex = STAGE_ORDER.indexOf(job.stage as (typeof STAGE_ORDER)[number]);
    return STAGE_ORDER.map((key, i) => ({
      key,
      status: i < currentIndex ? 'done' : i === currentIndex ? 'current' : 'pending',
    }));
  });

  constructor() {
    this.load(this.route.snapshot.paramMap.get('id')!);
  }

  protected async load(id: string): Promise<void> {
    this.error.set(null);
    try {
      this.job.set(await this.admin.getIngestionJob(id));
    } catch {
      this.error.set('admin.jobLoadFailed');
    }
  }

  protected async refresh(): Promise<void> {
    const current = this.job();
    if (current) {
      await this.load(current.id);
    }
  }
}
