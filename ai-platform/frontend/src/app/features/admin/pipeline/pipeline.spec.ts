import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminPipelineComponent } from './pipeline';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      pipeline: 'Indexing Pipeline', ingestTitle: 'Add a document', documentTitle: 'Title', documentContent: 'Content',
      ingest: 'Ingest', ingesting: 'Ingesting…', ingestFailed: 'Could not ingest this document.',
      stage: 'Stage', allStages: 'All stages', apply: 'Apply', noJobs: 'No indexing jobs yet.', chunks: 'Chunks',
      timestamp: 'Timestamp', accessDenied: "You don't have permission to view this section.",
      uploadFile: 'Upload a file', supportedFileTypes: 'Supported file types',
      titleOptionalHint: 'Title (optional)', upload: 'Upload', uploading: 'Uploading…',
      uploadFailed: 'Could not upload this file.',
    },
  },
};

type AdminStub = {
  listIngestionJobs: ReturnType<typeof vi.fn>;
  createIngestionJob: ReturnType<typeof vi.fn>;
  createIngestionJobFromFile: ReturnType<typeof vi.fn>;
};

describe('AdminPipelineComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      listIngestionJobs: vi.fn().mockResolvedValue([]),
      createIngestionJob: vi.fn(),
      createIngestionJobFromFile: vi.fn(),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminPipelineComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminPipelineComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders jobs on construction', async () => {
    const fixture = await setup({
      listIngestionJobs: vi.fn().mockResolvedValue([
        { id: 'j1', document_id: null, title: 'Doc A', stage: 'complete', chunk_count: 3, created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null },
      ]),
    });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="job-row-0"]')!.textContent).toContain('Doc A');
  });

  it('each job title links to its detail page', async () => {
    const fixture = await setup({
      listIngestionJobs: vi.fn().mockResolvedValue([
        { id: 'j1', document_id: null, title: 'Doc A', stage: 'complete', chunk_count: 3, created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null },
      ]),
    });
    const link = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="job-link-0"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/admin/pipeline/j1');
  });

  it('creating a job clears the form and refreshes the list', async () => {
    const listIngestionJobs = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([
      { id: 'j2', document_id: 'd2', title: 'New doc', stage: 'complete', chunk_count: 1, created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null },
    ]);
    const fixture = await setup({
      createIngestionJob: vi.fn().mockResolvedValue({ id: 'j2', title: 'New doc', stage: 'queued' }),
      listIngestionJobs,
    });
    const component = fixture.componentInstance as unknown as {
      ingestTitle: string; ingestContent: string; ingest: () => Promise<void>;
    };
    component.ingestTitle = 'New doc';
    component.ingestContent = 'some content';

    await component.ingest();
    fixture.detectChanges();

    expect(adminStub.createIngestionJob).toHaveBeenCalledWith('New doc', 'some content');
    expect(component.ingestTitle).toBe('');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="job-row-0"]')!.textContent).toContain('New doc');
  });

  it('applying a stage filter re-fetches with that stage', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { selectedStage: string; loadJobs: () => Promise<void> };
    component.selectedStage = 'failed';

    await component.loadJobs();

    expect(adminStub.listIngestionJobs).toHaveBeenLastCalledWith('failed');
  });

  it('uploading a file clears the form and refreshes the list', async () => {
    const file = new File(['hello world'], 'notes.txt', { type: 'text/plain' });
    const listIngestionJobs = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([
      { id: 'j3', document_id: 'd3', title: 'notes', stage: 'complete', chunk_count: 1, created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null },
    ]);
    const fixture = await setup({
      createIngestionJobFromFile: vi.fn().mockResolvedValue({ id: 'j3', title: 'notes', stage: 'queued' }),
      listIngestionJobs,
    });
    const component = fixture.componentInstance as unknown as {
      selectedFile: import('@angular/core').WritableSignal<File | null>;
      uploadTitle: string;
      upload: () => Promise<void>;
    };
    component.selectedFile.set(file);
    component.uploadTitle = '';

    await component.upload();
    fixture.detectChanges();

    expect(adminStub.createIngestionJobFromFile).toHaveBeenCalledWith(file, null);
    expect(component.selectedFile()).toBeNull();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="job-row-0"]')!.textContent).toContain('notes');
  });

  it('uploading with a typed title passes it through', async () => {
    const file = new File(['hello'], 'raw.md', { type: 'text/markdown' });
    const fixture = await setup({ createIngestionJobFromFile: vi.fn().mockResolvedValue({ id: 'j4', title: 'Custom', stage: 'queued' }) });
    const component = fixture.componentInstance as unknown as {
      selectedFile: import('@angular/core').WritableSignal<File | null>;
      uploadTitle: string;
      upload: () => Promise<void>;
    };
    component.selectedFile.set(file);
    component.uploadTitle = 'Custom';

    await component.upload();

    expect(adminStub.createIngestionJobFromFile).toHaveBeenCalledWith(file, 'Custom');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('a failed upload shows an error and keeps the selected file', async () => {
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' });
    const fixture = await setup({ createIngestionJobFromFile: vi.fn().mockRejectedValue(new Error('400')) });
    const component = fixture.componentInstance as unknown as {
      selectedFile: import('@angular/core').WritableSignal<File | null>;
      upload: () => Promise<void>;
    };
    component.selectedFile.set(file);

    await component.upload();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="upload-error"]')).toBeTruthy();
    expect(component.selectedFile()).toBe(file);
  });

  it('shows accessDenied when the load fails', async () => {
    const fixture = await setup({ listIngestionJobs: vi.fn().mockRejectedValue(new Error('403')) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="jobs-error"]')).toBeTruthy();
  });

  it('a failed create shows an error and does not clear the typed form', async () => {
    const fixture = await setup({ createIngestionJob: vi.fn().mockRejectedValue(new Error('400')) });
    const component = fixture.componentInstance as unknown as {
      ingestTitle: string; ingestContent: string; ingest: () => Promise<void>;
    };
    component.ingestTitle = 'Keep this';
    component.ingestContent = 'Keep this too';

    await component.ingest();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="ingest-error"]')).toBeTruthy();
    expect(component.ingestTitle).toBe('Keep this');
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('does nothing when submitting with an empty title or content', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as {
      ingestTitle: string; ingestContent: string; ingest: () => Promise<void>;
    };
    component.ingestTitle = '';
    component.ingestContent = 'has content but no title';

    await component.ingest();

    expect(adminStub.createIngestionJob).not.toHaveBeenCalled();
  });

  it('does nothing when uploading with no file selected', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { upload: () => Promise<void> };

    await component.upload();

    expect(adminStub.createIngestionJobFromFile).not.toHaveBeenCalled();
  });

  it('onFileSelected reads the file from the native input event', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as {
      onFileSelected: (e: Event) => void;
      selectedFile: import('@angular/core').WritableSignal<File | null>;
    };
    const file = new File(['x'], 'a.txt', { type: 'text/plain' });
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: [file] });

    component.onFileSelected({ target: input } as unknown as Event);

    expect(component.selectedFile()).toBe(file);
  });

  it('shows the empty-jobs message when nothing has been ingested', async () => {
    const fixture = await setup();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="jobs-empty"]')).toBeTruthy();
  });

  it('picks a distinct badge class per stage', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { badgeClass: (s: string) => string };
    const classes = new Set([
      component.badgeClass('complete'),
      component.badgeClass('failed'),
      component.badgeClass('queued'),
      component.badgeClass('chunking'),
    ]);
    expect(classes.size).toBe(4);
  });
});
