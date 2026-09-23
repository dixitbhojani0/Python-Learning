import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminPipelineDetailComponent, TrackerNode } from './pipeline-detail';
import { AdminService, IngestionJobDetail } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      backToPipeline: 'Back to pipeline', pipelineProgress: 'Pipeline progress', jobDetails: 'Job details',
      embeddingProvider: 'Embedding model', chunks: 'Chunks', startedAt: 'Started', finishedAt: 'Finished',
      viewDocument: 'View document', stageLog: 'Stage log', noStageLogYet: 'No stage activity recorded yet.',
      jobLoadFailed: 'Could not load this job.', refresh: 'Refresh',
    },
  },
};

function completeJob(overrides: Partial<IngestionJobDetail> = {}): IngestionJobDetail {
  return {
    id: 'j1',
    document_id: 'd1',
    title: 'Doc A',
    stage: 'complete',
    chunk_count: 3,
    created_at: '2026-01-01T00:00:00Z',
    started_at: '2026-01-01T00:00:01Z',
    finished_at: '2026-01-01T00:00:02Z',
    embedding_provider: 'mock',
    error_message: null,
    stage_log: [
      { stage: 'queued', message: "Stage 'queued' completed successfully.", at: '2026-01-01T00:00:00Z' },
      { stage: 'chunking', message: 'Chunking produced 1 piece(s).', at: '2026-01-01T00:00:01Z' },
      { stage: 'embedding', message: 'Embedding generation complete.', at: '2026-01-01T00:00:01Z' },
      { stage: 'storing', message: "Stage 'storing' completed successfully.", at: '2026-01-01T00:00:02Z' },
    ],
    ...overrides,
  };
}

type AdminStub = { getIngestionJob: ReturnType<typeof vi.fn> };

describe('AdminPipelineDetailComponent', () => {
  let adminStub: AdminStub;

  async function setup(id: string, overrides: Partial<AdminStub> = {}) {
    adminStub = { getIngestionJob: vi.fn().mockResolvedValue(completeJob()), ...overrides };

    await TestBed.configureTestingModule({
      imports: [
        AdminPipelineDetailComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [
        provideRouter([]),
        { provide: AdminService, useValue: adminStub },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: convertToParamMap({ id }) } } },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminPipelineDetailComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads the job by the route id and renders its title and stage', async () => {
    const fixture = await setup('j1');
    expect(adminStub.getIngestionJob).toHaveBeenCalledWith('j1');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="job-detail-title"]')!.textContent).toContain('Doc A');
    expect(el.querySelector('[data-testid="job-detail-stage-badge"]')!.textContent).toContain('complete');
  });

  it('renders a stage-log entry per real transition', async () => {
    const fixture = await setup('j1');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="stage-log-0"]')!.textContent).toContain('queued');
    expect(el.querySelector('[data-testid="stage-log-3"]')!.textContent).toContain('storing');
  });

  it('links to the resulting document when one exists', async () => {
    const fixture = await setup('j1');
    const link = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="job-detail-document-link"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/admin/documents/d1');
  });

  it('a completed job marks every tracker node as done', async () => {
    const fixture = await setup('j1');
    const component = fixture.componentInstance as unknown as { trackerNodes: () => TrackerNode[] };
    expect(component.trackerNodes().every((n) => n.status === 'done')).toBe(true);
    expect(component.trackerNodes().map((n) => n.key)).toEqual(['queued', 'chunking', 'embedding', 'storing', 'complete']);
  });

  it('a failed job marks the failed stage and everything after it correctly', async () => {
    const fixture = await setup('j1', {
      getIngestionJob: vi.fn().mockResolvedValue(
        completeJob({
          stage: 'failed',
          document_id: null,
          error_message: 'GEMINI_API_KEY is not set',
          stage_log: [
            { stage: 'queued', message: "Stage 'queued' completed successfully.", at: '2026-01-01T00:00:00Z' },
            { stage: 'chunking', message: 'Chunking produced 1 piece(s).', at: '2026-01-01T00:00:01Z' },
            { stage: 'embedding', message: 'Failed: GEMINI_API_KEY is not set', at: '2026-01-01T00:00:02Z' },
          ],
        })
      ),
    });

    const component = fixture.componentInstance as unknown as { trackerNodes: () => TrackerNode[] };
    const nodes = component.trackerNodes();
    expect(nodes.find((n) => n.key === 'queued')!.status).toBe('done');
    expect(nodes.find((n) => n.key === 'chunking')!.status).toBe('done');
    expect(nodes.find((n) => n.key === 'embedding')!.status).toBe('failed');
    expect(nodes.find((n) => n.key === 'storing')!.status).toBe('pending');
    expect(nodes.find((n) => n.key === 'complete')!.status).toBe('pending');

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="job-detail-error-message"]')!.textContent).toContain('GEMINI_API_KEY');
    expect(el.querySelector('[data-testid="job-detail-document-link"]')).toBeNull();
  });

  it('an in-progress job marks the current stage distinctly from done/pending ones', async () => {
    const fixture = await setup('j1', {
      getIngestionJob: vi.fn().mockResolvedValue(
        completeJob({
          stage: 'embedding',
          document_id: null,
          finished_at: null,
          stage_log: [
            { stage: 'queued', message: "Stage 'queued' completed successfully.", at: '2026-01-01T00:00:00Z' },
            { stage: 'chunking', message: 'Chunking produced 1 piece(s).', at: '2026-01-01T00:00:01Z' },
          ],
        })
      ),
    });

    const component = fixture.componentInstance as unknown as { trackerNodes: () => TrackerNode[] };
    const nodes = component.trackerNodes();
    expect(nodes.find((n) => n.key === 'chunking')!.status).toBe('done');
    expect(nodes.find((n) => n.key === 'embedding')!.status).toBe('current');
    expect(nodes.find((n) => n.key === 'storing')!.status).toBe('pending');
  });

  it('refresh() re-fetches the same job', async () => {
    const fixture = await setup('j1');
    const component = fixture.componentInstance as unknown as { refresh: () => Promise<void> };

    await component.refresh();

    expect(adminStub.getIngestionJob).toHaveBeenCalledTimes(2);
    expect(adminStub.getIngestionJob).toHaveBeenLastCalledWith('j1');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows an error and no content when the job cannot be loaded', async () => {
    const fixture = await setup('j1', { getIngestionJob: vi.fn().mockRejectedValue(new Error('404')) });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="job-detail-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="job-detail-title"]')).toBeNull();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('shows the no-stage-log message when a freshly queued job has no log entries yet', async () => {
    const fixture = await setup('j1', {
      getIngestionJob: vi.fn().mockResolvedValue(
        completeJob({ stage: 'queued', document_id: null, started_at: null, finished_at: null, stage_log: [] })
      ),
    });
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('No stage activity recorded yet.');
  });
});
