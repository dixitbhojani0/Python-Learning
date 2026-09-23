import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminAuditComponent } from './audit';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      auditLog: 'Audit & logs', category: 'Category', allCategories: 'All categories', apply: 'Apply',
      timestamp: 'Timestamp', details: 'Details', noAuditEvents: 'No events recorded yet.',
      accessDenied: "You don't have permission to view this section.",
    },
  },
};

type AdminStub = { listAuditLog: ReturnType<typeof vi.fn> };

describe('AdminAuditComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      listAuditLog: vi.fn().mockResolvedValue([]),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminAuditComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [{ provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminAuditComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders events on construction with no filter', async () => {
    const fixture = await setup({
      listAuditLog: vi.fn().mockResolvedValue([
        { id: 'e1', event_type: 'chat_turn', payload: { conversation_id: 'c1' }, created_at: '2026-01-01T00:00:00Z' },
      ]),
    });

    expect(adminStub.listAuditLog).toHaveBeenCalledWith(null);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="audit-row-0"]')!.textContent).toContain('chat_turn');
  });

  it('applying a category filter re-fetches with that event type', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { selectedEventType: string; load: () => Promise<void> };
    component.selectedEventType = 'tool_call_rejected';

    await component.load();

    expect(adminStub.listAuditLog).toHaveBeenLastCalledWith('tool_call_rejected');
  });

  it('renders the payload as JSON in the details column', async () => {
    const fixture = await setup({
      listAuditLog: vi.fn().mockResolvedValue([
        { id: 'e1', event_type: 'tool_call', payload: { tool_name: 'calculator', success: true }, created_at: '2026-01-01T00:00:00Z' },
      ]),
    });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="audit-row-0"]')!.textContent).toContain('calculator');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows accessDenied when the load fails', async () => {
    const fixture = await setup({ listAuditLog: vi.fn().mockRejectedValue(new Error('403')) });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="audit-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="audit-table"]')).toBeNull();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('shows the empty-events message when nothing has been recorded', async () => {
    const fixture = await setup();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="audit-empty"]')).toBeTruthy();
  });

  it('picks a distinct badge class per event type', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { eventBadgeClass: (t: string) => string };
    const classes = new Set([
      component.eventBadgeClass('tool_call_rejected'),
      component.eventBadgeClass('tool_call_approved'),
      component.eventBadgeClass('tool_call_pending_approval'),
      component.eventBadgeClass('chat_turn'),
      component.eventBadgeClass('tool_call'),
    ]);
    expect(classes.size).toBe(5);
  });
});
