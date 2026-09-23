import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminTeamComponent } from './team';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      team: 'Team & roles', users: 'Team members', email: 'Email', role: 'Role',
      inviteEmailPlaceholder: 'person@company.com', invite: 'Invite', inviting: 'Inviting…',
      inviteFailed: 'Could not invite this person.', roleChangeFailed: "Could not change this person's role.",
      invitedNotice: 'Invited {{email}}. Temporary password: {{password}}', accessDenied: "You don't have permission to view this section.",
      roles: 'Roles', newRoleNamePlaceholder: 'Role name', permissions: 'Permissions',
      createRole: 'Create role', creatingRole: 'Creating…', createRoleFailed: 'Could not create this role.',
      permissionLabels: {
        'users:read': 'View team members', 'users:write': 'Manage team members and roles',
        'documents:read': 'View knowledge base', 'documents:write': 'Manage knowledge base',
        'tools:approve': 'Approve tool actions',
      },
    },
  },
};

type AdminStub = {
  listUsers: ReturnType<typeof vi.fn>;
  listRoles: ReturnType<typeof vi.fn>;
  inviteUser: ReturnType<typeof vi.fn>;
  createRole: ReturnType<typeof vi.fn>;
  updateUserRole: ReturnType<typeof vi.fn>;
};

describe('AdminTeamComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      listUsers: vi.fn().mockResolvedValue([]),
      listRoles: vi.fn().mockResolvedValue([{ id: 'r1', name: 'admin', permissions: ['users:read', 'users:write'] }]),
      inviteUser: vi.fn(),
      createRole: vi.fn(),
      updateUserRole: vi.fn(),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminTeamComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [{ provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminTeamComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders team members and roles on construction', async () => {
    const fixture = await setup({
      listUsers: vi.fn().mockResolvedValue([
        { id: 'u1', email: 'a@acme.test', role_id: 'r1', role_name: 'admin', created_at: '2026-01-01T00:00:00Z' },
      ]),
    });

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="user-0"]')!.textContent).toContain('a@acme.test');
    expect(el.querySelector('[data-testid="role-0"]')!.textContent).toContain('admin');
  });

  it('inviting a user clears the email field, refreshes the list, and shows the one-time password', async () => {
    const listUsers = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([
      { id: 'u2', email: 'new@acme.test', role_id: 'r1', role_name: 'admin', created_at: '2026-01-01T00:00:00Z' },
    ]);
    const fixture = await setup({
      inviteUser: vi.fn().mockResolvedValue({ id: 'u2', email: 'new@acme.test', role_id: 'r1', temporary_password: 'temp-pw-123' }),
      listUsers,
    });
    const component = fixture.componentInstance as unknown as {
      inviteEmail: string; inviteRoleId: string; inviteUser: () => Promise<void>;
    };
    component.inviteEmail = 'new@acme.test';
    component.inviteRoleId = 'r1';

    await component.inviteUser();
    fixture.detectChanges();

    expect(adminStub.inviteUser).toHaveBeenCalledWith('new@acme.test', 'r1');
    expect(component.inviteEmail).toBe('');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="invite-success"]')!.textContent).toContain('temp-pw-123');
    expect(el.querySelector('[data-testid="user-0"]')!.textContent).toContain('new@acme.test');
  });

  it("changing a user's role calls the service and updates that row only", async () => {
    const fixture = await setup({
      listUsers: vi.fn().mockResolvedValue([
        { id: 'u1', email: 'a@acme.test', role_id: 'r1', role_name: 'admin', created_at: '2026-01-01T00:00:00Z' },
        { id: 'u2', email: 'b@acme.test', role_id: 'r1', role_name: 'admin', created_at: '2026-01-01T00:00:00Z' },
      ]),
      listRoles: vi.fn().mockResolvedValue([
        { id: 'r1', name: 'admin', permissions: ['users:read', 'users:write'] },
        { id: 'r2', name: 'member', permissions: [] },
      ]),
      updateUserRole: vi.fn().mockResolvedValue({ id: 'u1', email: 'a@acme.test', role_id: 'r2', role_name: 'member', created_at: '2026-01-01T00:00:00Z' }),
    });
    const component = fixture.componentInstance as unknown as { changeUserRole: (userId: string, roleId: string) => Promise<void> };

    await component.changeUserRole('u1', 'r2');
    fixture.detectChanges();

    expect(adminStub.updateUserRole).toHaveBeenCalledWith('u1', 'r2');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="user-0"]')!.textContent).toContain('a@acme.test');
    expect(el.querySelector('[data-testid="user-1"]')!.textContent).toContain('b@acme.test');
  });

  it('creating a role clears the form, refreshes the list, and sends only the checked permissions', async () => {
    const listRoles = vi.fn().mockResolvedValueOnce([{ id: 'r1', name: 'admin', permissions: ['users:read'] }])
      .mockResolvedValue([
        { id: 'r1', name: 'admin', permissions: ['users:read'] },
        { id: 'r2', name: 'member', permissions: ['documents:read'] },
      ]);
    const fixture = await setup({
      createRole: vi.fn().mockResolvedValue({ id: 'r2', name: 'member', permissions: ['documents:read'] }),
      listRoles,
    });
    const component = fixture.componentInstance as unknown as {
      newRoleName: string; togglePermission: (p: string) => void; createRole: () => Promise<void>;
    };
    component.newRoleName = 'member';
    component.togglePermission('documents:read');

    await component.createRole();
    fixture.detectChanges();

    expect(adminStub.createRole).toHaveBeenCalledWith('member', ['documents:read']);
    expect(component.newRoleName).toBe('');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="role-1"]')!.textContent).toContain('member');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows accessDenied for users alone when that load fails', async () => {
    const fixture = await setup({ listUsers: vi.fn().mockRejectedValue(new Error('403')) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="users-error"]')).toBeTruthy();
  });

  it('shows accessDenied for roles alone when that load fails', async () => {
    const fixture = await setup({ listRoles: vi.fn().mockRejectedValue(new Error('403')) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="roles-error"]')).toBeTruthy();
  });

  it('a failed invite shows an error and does not clear the typed email', async () => {
    const fixture = await setup({ inviteUser: vi.fn().mockRejectedValue(new Error('400')) });
    const component = fixture.componentInstance as unknown as {
      inviteEmail: string; inviteRoleId: string; inviteUser: () => Promise<void>;
    };
    component.inviteEmail = 'keep@acme.test';
    component.inviteRoleId = 'r1';

    await component.inviteUser();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="invite-error"]')).toBeTruthy();
    expect(component.inviteEmail).toBe('keep@acme.test');
  });

  it('a failed role change shows an error without altering the user list', async () => {
    const fixture = await setup({
      listUsers: vi.fn().mockResolvedValue([{ id: 'u1', email: 'a@acme.test', role_id: 'r1', role_name: 'admin', created_at: '2026-01-01T00:00:00Z' }]),
      updateUserRole: vi.fn().mockRejectedValue(new Error('404')),
    });
    const component = fixture.componentInstance as unknown as { changeUserRole: (userId: string, roleId: string) => Promise<void> };

    await component.changeUserRole('u1', 'does-not-exist');
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="users-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="user-0"]')!.textContent).toContain('admin');
  });

  it('a failed role creation shows an error and does not clear the typed name', async () => {
    const fixture = await setup({ createRole: vi.fn().mockRejectedValue(new Error('400')) });
    const component = fixture.componentInstance as unknown as { newRoleName: string; createRole: () => Promise<void> };
    component.newRoleName = 'keep-me';

    await component.createRole();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="create-role-error"]')).toBeTruthy();
    expect(component.newRoleName).toBe('keep-me');
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('does nothing when inviting with an empty email or no role selected', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as {
      inviteEmail: string; inviteRoleId: string; inviteUser: () => Promise<void>;
    };
    component.inviteEmail = '';
    component.inviteRoleId = 'r1';
    await component.inviteUser();

    component.inviteEmail = 'someone@acme.test';
    component.inviteRoleId = '';
    await component.inviteUser();

    expect(adminStub.inviteUser).not.toHaveBeenCalled();
  });

  it('does nothing when creating a role with an empty name', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { newRoleName: string; createRole: () => Promise<void> };
    component.newRoleName = '   ';

    await component.createRole();

    expect(adminStub.createRole).not.toHaveBeenCalled();
  });

  it('togglePermission adds then removes a permission from the pending set', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as {
      togglePermission: (p: string) => void; newRolePermissions: () => Set<string>;
    };

    component.togglePermission('tools:approve');
    expect(component.newRolePermissions().has('tools:approve')).toBe(true);

    component.togglePermission('tools:approve');
    expect(component.newRolePermissions().has('tools:approve')).toBe(false);
  });

  it('shows no rows when no users or roles are loaded yet', async () => {
    const fixture = await setup({ listUsers: vi.fn().mockResolvedValue([]), listRoles: vi.fn().mockResolvedValue([]) });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="user-0"]')).toBeNull();
    expect(el.querySelector('[data-testid="role-0"]')).toBeNull();
  });
});
