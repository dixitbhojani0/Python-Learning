import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminDocumentDetailComponent } from './document-detail';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      backToDocuments: 'Back to documents', chunkCount: '{{count}} chunks', ingestedOn: 'Ingested on',
      documentContent: 'Content', documentLoadFailed: 'Could not load this document.',
    },
  },
};

type AdminStub = { getDocument: ReturnType<typeof vi.fn> };

describe('AdminDocumentDetailComponent', () => {
  let adminStub: AdminStub;

  async function setup(id: string, overrides: Partial<AdminStub> = {}) {
    adminStub = {
      getDocument: vi.fn().mockResolvedValue({
        id, title: 'Doc A', content: 'the full content', chunk_count: 3, created_at: '2026-01-01T00:00:00Z',
      }),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminDocumentDetailComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [
        provideRouter([]),
        { provide: AdminService, useValue: adminStub },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: convertToParamMap({ id }) } } },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminDocumentDetailComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads the document by the route id and renders its content', async () => {
    const fixture = await setup('d1');
    expect(adminStub.getDocument).toHaveBeenCalledWith('d1');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="document-detail-title"]')!.textContent).toContain('Doc A');
    expect(el.querySelector('[data-testid="document-detail-content"]')!.textContent).toContain('the full content');
  });

  it('renders the chunk count', async () => {
    const fixture = await setup('d1');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('3 chunks');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows an error and no content when the document cannot be loaded', async () => {
    const fixture = await setup('d1', { getDocument: vi.fn().mockRejectedValue(new Error('404')) });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="document-detail-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="document-detail-title"]')).toBeNull();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('requests the document id taken from the route, not a hardcoded one', async () => {
    const fixture = await setup('some-other-id');
    expect(adminStub.getDocument).toHaveBeenCalledWith('some-other-id');
    expect(fixture).toBeTruthy();
  });
});
