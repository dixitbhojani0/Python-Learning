import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminApprovalsComponent } from './approvals';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      pendingApprovals: 'Pending tool approvals', noPendingApprovals: 'Nothing is waiting for approval.',
      approve: 'Approve', reject: 'Reject', approvalActionFailed: 'Could not complete that action.',
      accessDenied: "You don't have permission to view this section.",
    },
  },
};

type AdminStub = {
  listPendingApprovals: ReturnType<typeof vi.fn>;
  approveToolCall: ReturnType<typeof vi.fn>;
  rejectToolCall: ReturnType<typeof vi.fn>;
};

describe('AdminApprovalsComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      listPendingApprovals: vi.fn().mockResolvedValue([]),
      approveToolCall: vi.fn(),
      rejectToolCall: vi.fn(),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminApprovalsComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [{ provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminApprovalsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

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

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows accessDenied when the load fails', async () => {
    const fixture = await setup({ listPendingApprovals: vi.fn().mockRejectedValue(new Error('403')) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approvals-error"]')).toBeTruthy();
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

  // ── Edge ────────────────────────────────────────────────────────────────

  it('shows the empty-approvals message when nothing is pending', async () => {
    const fixture = await setup();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="approvals-empty"]')).toBeTruthy();
  });
});
