import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminOverviewComponent } from './overview';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      overview: 'Overview', tenant: 'Workspace', providers: 'AI providers',
      llmProvider: 'Chat model', embeddingProvider: 'Embedding model',
      accessDenied: "You don't have permission to view this section.",
      telemetry: 'Usage', totalChatTurns: 'Chat turns', ragUsageRate: 'RAG usage rate',
      memoryUsageRate: 'Memory usage rate', avgLatency: 'Avg. latency (ms)', avgResponseLength: 'Avg. response length (words)',
    },
  },
};

type AdminStub = {
  getTenant: ReturnType<typeof vi.fn>;
  getProviders: ReturnType<typeof vi.fn>;
  getTelemetry: ReturnType<typeof vi.fn>;
};

describe('AdminOverviewComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      getTenant: vi.fn().mockResolvedValue({ id: 't1', name: 'Acme', slug: 'acme', compliance_profile: 'standard' }),
      getProviders: vi.fn().mockResolvedValue({
        llm_provider: 'mock', embedding_provider: 'mock', available_llm_providers: ['mock'], available_embedding_providers: ['mock'],
      }),
      getTelemetry: vi.fn().mockResolvedValue({
        total_chat_turns: 0, rag_usage_rate: 0, memory_usage_rate: 0, avg_latency_ms: 0, avg_response_word_count: 0,
      }),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminOverviewComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [{ provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminOverviewComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders tenant, providers, and telemetry on construction', async () => {
    const fixture = await setup();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="tenant-info"]')!.textContent).toContain('Acme');
    expect(el.querySelector('[data-testid="provider-info"]')!.textContent).toContain('mock');
    expect(el.querySelector('[data-testid="telemetry-info"]')).toBeTruthy();
  });

  it('renders telemetry stats when the load succeeds', async () => {
    const fixture = await setup({
      getTelemetry: vi.fn().mockResolvedValue({
        total_chat_turns: 5, rag_usage_rate: 0.4, memory_usage_rate: 0.2, avg_latency_ms: 12.5, avg_response_word_count: 8.5,
      }),
    });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="telemetry-info"]')!.textContent).toContain('5');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows accessDenied only for the section whose load failed, not all three', async () => {
    const fixture = await setup({ getTenant: vi.fn().mockRejectedValue(new Error('403')) });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="tenant-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="provider-info"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="telemetry-info"]')).toBeTruthy();
  });

  it('shows accessDenied for providers alone when that load fails', async () => {
    const fixture = await setup({ getProviders: vi.fn().mockRejectedValue(new Error('403')) });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="providers-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="tenant-info"]')).toBeTruthy();
  });

  it('shows accessDenied for telemetry alone when only that permission is missing', async () => {
    const fixture = await setup({ getTelemetry: vi.fn().mockRejectedValue(new Error('403')) });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="telemetry-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="tenant-info"]')).toBeTruthy();
  });
});
