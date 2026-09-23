import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminDocumentsComponent } from './documents';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      documents: 'Knowledge base', noDocuments: 'No documents ingested yet.', chunkCount: '{{count}} chunks',
      delete: 'Delete', deleteFailed: 'Could not delete this document.', accessDenied: "You don't have permission to view this section.",
      addDocumentHint: 'Add documents from the', pipeline: 'Indexing Pipeline',
    },
  },
};

type AdminStub = {
  listDocuments: ReturnType<typeof vi.fn>;
  deleteDocument: ReturnType<typeof vi.fn>;
};

describe('AdminDocumentsComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      listDocuments: vi.fn().mockResolvedValue([]),
      deleteDocument: vi.fn(),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminDocumentsComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminDocumentsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders documents on construction', async () => {
    const fixture = await setup({ listDocuments: vi.fn().mockResolvedValue([{ id: 'd1', title: 'Doc A', chunk_count: 2 }]) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="document-0"]')!.textContent).toContain('Doc A');
  });

  it('each document title links to its detail page', async () => {
    const fixture = await setup({ listDocuments: vi.fn().mockResolvedValue([{ id: 'd1', title: 'Doc A', chunk_count: 2 }]) });
    const link = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="document-link-0"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/admin/documents/d1');
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

  it('shows accessDenied for documents when the load fails', async () => {
    const fixture = await setup({ listDocuments: vi.fn().mockRejectedValue(new Error('403')) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="documents-error"]')).toBeTruthy();
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
    expect(el.querySelector('[data-testid="document-0"]')).toBeTruthy();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('shows the empty-documents message when the tenant has ingested nothing', async () => {
    const fixture = await setup();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="documents-empty"]')).toBeTruthy();
  });
});
