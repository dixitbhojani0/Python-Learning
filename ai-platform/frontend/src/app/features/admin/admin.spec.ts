import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminComponent } from './admin';
import { AdminService } from '../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      title: 'Admin', tenant: 'Workspace', providers: 'AI providers', documents: 'Knowledge base',
      llmProvider: 'Chat model', embeddingProvider: 'Embedding model', noDocuments: 'No documents ingested yet.',
      chunkCount: '{{count}} chunks', delete: 'Delete', ingestTitle: 'Add a document', documentTitle: 'Title',
      documentContent: 'Content', ingest: 'Ingest', ingesting: 'Ingesting…',
      ingestFailed: 'Could not ingest this document.', deleteFailed: 'Could not delete this document.',
      accessDenied: "You don't have permission to view this section.", backToChat: 'Back to chat',
      telemetry: 'Usage', totalChatTurns: 'Chat turns', ragUsageRate: 'RAG usage rate',
      memoryUsageRate: 'Memory usage rate', avgLatency: 'Avg. latency (ms)', avgResponseLength: 'Avg. response length (words)',
      pendingApprovals: 'Pending tool approvals', noPendingApprovals: 'Nothing is waiting for approval.',
      approve: 'Approve', reject: 'Reject', approvalActionFailed: 'Could not complete that action.',
    },
  },
};

type AdminStub = {
  getTenant: ReturnType<typeof vi.fn>;
  getProviders: ReturnType<typeof vi.fn>;
  listDocuments: ReturnType<typeof vi.fn>;
  deleteDocument: ReturnType<typeof vi.fn>;
  ingestDocument: ReturnType<typeof vi.fn>;
  getTelemetry: ReturnType<typeof vi.fn>;
  listPendingApprovals: ReturnType<typeof vi.fn>;
  approveToolCall: ReturnType<typeof vi.fn>;
  rejectToolCall: ReturnType<typeof vi.fn>;
};

describe('AdminComponent', () => {
  let adminStub: AdminStub;

  // Overrides are supplied BEFORE the component (and its constructor-time
  // loads) exist — configuring a stub after setup() would land on a stale
  // object the component never sees (the exact bug this pattern avoids,
  // already hit once in chat.spec.ts/login.spec.ts).
  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      getTenant: vi.fn().mockResolvedValue({ id: 't1', name: 'Acme', slug: 'acme', compliance_profile: 'standard' }),
      getProviders: vi.fn().mockResolvedValue({
        llm_provider: 'mock', embedding_provider: 'mock', available_llm_providers: ['mock'], available_embedding_providers: ['mock'],
      }),
      listDocuments: vi.fn().mockResolvedValue([]),
      deleteDocument: vi.fn(),
      ingestDocument: vi.fn(),
      getTelemetry: vi.fn().mockResolvedValue({
        total_chat_turns: 0, rag_usage_rate: 0, memory_usage_rate: 0, avg_latency_ms: 0, avg_response_word_count: 0,
      }),
      listPendingApprovals: vi.fn().mockResolvedValue([]),
      approveToolCall: vi.fn(),
      rejectToolCall: vi.fn(),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminComponent);
    fixture.detectChanges();
    await fixture.whenStable(); // constructor's three async loads
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders tenant, providers, and documents on construction', async () => {
    const fixture = await setup({
      listDocuments: vi.fn().mockResolvedValue([{ id: 'd1', title: 'Doc A', chunk_count: 2 }]),
    });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="tenant-info"]')!.textContent).toContain('Acme');
    expect(el.querySelector('[data-testid="provider-info"]')!.textContent).toContain('mock');
    expect(el.querySelector('[data-testid="document-0"]')!.textContent).toContain('Doc A');
  });

  it('ingesting a document clears the form and refreshes the list', async () => {
    const listDocuments = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([{ id: 'd2', title: 'New doc', chunk_count: 1 }]);
    const fixture = await setup({
      ingestDocument: vi.fn().mockResolvedValue({ id: 'd2', title: 'New doc', chunk_count: 1 }),
      listDocuments,
    });
    const component = fixture.componentInstance as unknown as {
      ingestTitle: string; ingestContent: string; ingest: () => Promise<void>;
    };
    component.ingestTitle = 'New doc';
    component.ingestContent = 'Some content';

    await component.ingest();
    fixture.detectChanges();

    expect(adminStub.ingestDocument).toHaveBeenCalledWith('New doc', 'Some content');
    expect(component.ingestTitle).toBe('');
    expect(component.ingestContent).toBe('');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="document-0"]')!.textContent).toContain('New doc');
  });

  it('deleting a document removes only that document from the list', async () => {
    const fixture = await setup({
      listDocuments: vi.fn().mockResolvedValue([
        { id: 'd1', title: 'Keep me', chunk_count: 1 },
        { id: 'd2', title: 'Delete me', chunk_count: 1 },
      ]),
      deleteDocument: vi.fn().mockResolvedValue({ deleted: 'd2' }),
    });
    const component = fixture.componentInstance as unknown as { deleteDocument: (id: string) => Promise<void> };

    await component.deleteDocument('d2');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Keep me');
    expect(el.textContent).not.toContain('Delete me');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows accessDenied only for the section whose load failed, not all three', async () => {
    const fixture = await setup({
      getTenant: vi.fn().mockRejectedValue(new Error('403')),
      listDocuments: vi.fn().mockResolvedValue([{ id: 'd1', title: 'Doc A', chunk_count: 1 }]),
    });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="tenant-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="provider-info"]')).toBeTruthy(); // providers section unaffected
    expect(el.querySelector('[data-testid="document-0"]')).toBeTruthy(); // documents section unaffected
  });

  it('ingest failure shows an error and does not clear the typed form', async () => {
    const fixture = await setup({ ingestDocument: vi.fn().mockRejectedValue(new Error('500')) });
    const component = fixture.componentInstance as unknown as {
      ingestTitle: string; ingestContent: string; ingest: () => Promise<void>;
    };
    component.ingestTitle = 'Keep this';
    component.ingestContent = 'Keep this too';

    await component.ingest();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="ingest-error"]')).toBeTruthy();
    expect(component.ingestTitle).toBe('Keep this'); // not wiped out by the failed attempt
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

    expect(adminStub.ingestDocument).not.toHaveBeenCalled();
  });

  it('shows the empty-documents message when the tenant has ingested nothing', async () => {
    const fixture = await setup({ listDocuments: vi.fn().mockResolvedValue([]) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="documents-empty"]')).toBeTruthy();
  });

  it('renders telemetry stats when the load succeeds', async () => {
    const fixture = await setup({
      getTelemetry: vi.fn().mockResolvedValue({
        total_chat_turns: 5, rag_usage_rate: 0.4, memory_usage_rate: 0.2, avg_latency_ms: 12.5, avg_response_word_count: 8.5,
      }),
    });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="telemetry-info"]')!.textContent).toContain('5');
  });

  it('shows accessDenied for telemetry alone when only that permission is missing', async () => {
    const fixture = await setup({ getTelemetry: vi.fn().mockRejectedValue(new Error('403')) });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="telemetry-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="tenant-info"]')).toBeTruthy(); // other sections unaffected
  });

  // ── Pending tool approvals (Phase 11) ────────────────────────────────

  it('renders a pending approval with approve/reject actions', async () => {
    const fixture = await setup({
      listPendingApprovals: vi.fn().mockResolvedValue([
        { id: 'a1', tool_name: 'delete_all_documents', tool_args: {}, status: 'pending', created_at: '2026-01-01T00:00:00Z' },
      ]),
    });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approval-0"]')!.textContent).toContain(
      'delete_all_documents'
    );
  });

  it('approving a pending approval removes it from the list', async () => {
    const fixture = await setup({
      listPendingApprovals: vi.fn().mockResolvedValue([
        { id: 'a1', tool_name: 'delete_all_documents', tool_args: {}, status: 'pending', created_at: '2026-01-01T00:00:00Z' },
      ]),
      approveToolCall: vi.fn().mockResolvedValue({ status: 'approved', result: {} }),
    });
    const component = fixture.componentInstance as unknown as { approveToolCall: (id: string) => Promise<void> };

    await component.approveToolCall('a1');
    fixture.detectChanges();

    expect(adminStub.approveToolCall).toHaveBeenCalledWith('a1');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approval-0"]')).toBeNull();
  });

  it('rejecting a pending approval removes it from the list', async () => {
    const fixture = await setup({
      listPendingApprovals: vi.fn().mockResolvedValue([
        { id: 'a1', tool_name: 'delete_all_documents', tool_args: {}, status: 'pending', created_at: '2026-01-01T00:00:00Z' },
      ]),
      rejectToolCall: vi.fn().mockResolvedValue({ status: 'rejected' }),
    });
    const component = fixture.componentInstance as unknown as { rejectToolCall: (id: string) => Promise<void> };

    await component.rejectToolCall('a1');
    fixture.detectChanges();

    expect(adminStub.rejectToolCall).toHaveBeenCalledWith('a1');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approval-0"]')).toBeNull();
  });

  it('shows the empty-approvals message when nothing is pending', async () => {
    const fixture = await setup({ listPendingApprovals: vi.fn().mockResolvedValue([]) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approvals-empty"]')).toBeTruthy();
  });

  it('a failed approve shows an error without crashing the section', async () => {
    const fixture = await setup({
      listPendingApprovals: vi.fn().mockResolvedValue([
        { id: 'a1', tool_name: 'delete_all_documents', tool_args: {}, status: 'pending', created_at: '2026-01-01T00:00:00Z' },
      ]),
      approveToolCall: vi.fn().mockRejectedValue(new Error('500')),
    });
    const component = fixture.componentInstance as unknown as { approveToolCall: (id: string) => Promise<void> };

    await component.approveToolCall('a1');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approvals-error"]')).toBeTruthy();
  });

  it('a failed reject shows an error without crashing the section', async () => {
    const fixture = await setup({
      listPendingApprovals: vi.fn().mockResolvedValue([
        { id: 'a1', tool_name: 'delete_all_documents', tool_args: {}, status: 'pending', created_at: '2026-01-01T00:00:00Z' },
      ]),
      rejectToolCall: vi.fn().mockRejectedValue(new Error('500')),
    });
    const component = fixture.componentInstance as unknown as { rejectToolCall: (id: string) => Promise<void> };

    await component.rejectToolCall('a1');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approvals-error"]')).toBeTruthy();
  });

  it('shows accessDenied for providers alone when that load fails', async () => {
    const fixture = await setup({ getProviders: vi.fn().mockRejectedValue(new Error('403')) });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="providers-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="tenant-info"]')).toBeTruthy(); // other sections unaffected
  });

  it('shows accessDenied for documents alone when that load fails', async () => {
    const fixture = await setup({ listDocuments: vi.fn().mockRejectedValue(new Error('403')) });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="documents-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="tenant-info"]')).toBeTruthy();
  });

  it('shows accessDenied for pending approvals alone when that load fails', async () => {
    const fixture = await setup({ listPendingApprovals: vi.fn().mockRejectedValue(new Error('403')) });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="approvals-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="tenant-info"]')).toBeTruthy();
  });

  it('a failed delete shows an error without removing the document from the list', async () => {
    const fixture = await setup({
      listDocuments: vi.fn().mockResolvedValue([{ id: 'd1', title: 'Doc A', chunk_count: 1 }]),
      deleteDocument: vi.fn().mockRejectedValue(new Error('500')),
    });
    const component = fixture.componentInstance as unknown as { deleteDocument: (id: string) => Promise<void> };

    await component.deleteDocument('d1');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="documents-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="document-0"]')).toBeTruthy(); // not removed — the delete never actually succeeded
  });
});
