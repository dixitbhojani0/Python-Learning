import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, ManagedUser, Role } from '../../../core/admin/admin.service';

// Mirrors backend/app/api/admin_routes.py's KNOWN_PERMISSIONS exactly — the
// fixed vocabulary of permission strings any route in this codebase actually
// checks. Kept as a plain constant, not fetched from the backend, because it
// changes only when a new permission-gated route is added to the backend
// itself (a code change on both sides already, not independent drift).
const KNOWN_PERMISSIONS = ['users:read', 'users:write', 'documents:read', 'documents:write', 'tools:approve'] as const;

@Component({
  selector: 'app-admin-team',
  imports: [FormsModule, TranslocoPipe],
  templateUrl: './team.html',
})
export class AdminTeamComponent {
  private readonly admin = inject(AdminService);

  protected readonly users = signal<ManagedUser[]>([]);
  protected readonly usersError = signal<string | null>(null);
  protected readonly roles = signal<Role[]>([]);
  protected readonly rolesError = signal<string | null>(null);
  protected readonly knownPermissions = KNOWN_PERMISSIONS;

  protected inviteEmail = '';
  protected inviteRoleId = '';
  protected readonly inviting = signal(false);
  protected readonly inviteError = signal<string | null>(null);
  // The one-time temporary password handed back by a successful invite —
  // there is no email/SMTP infrastructure to deliver it any other way, so
  // it must be shown once here for the admin to copy and share themselves.
  protected readonly lastInvitedUser = signal<{ email: string; password: string } | null>(null);

  protected newRoleName = '';
  protected readonly newRolePermissions = signal<Set<string>>(new Set());
  protected readonly creatingRole = signal(false);
  protected readonly createRoleError = signal<string | null>(null);

  constructor() {
    this.loadUsers();
    this.loadRoles();
  }

  private async loadUsers(): Promise<void> {
    try {
      this.users.set(await this.admin.listUsers());
    } catch {
      this.usersError.set('admin.accessDenied');
    }
  }

  private async loadRoles(): Promise<void> {
    try {
      this.roles.set(await this.admin.listRoles());
    } catch {
      this.rolesError.set('admin.accessDenied');
    }
  }

  protected async inviteUser(): Promise<void> {
    const email = this.inviteEmail.trim();
    if (!email || !this.inviteRoleId || this.inviting()) {
      return;
    }

    this.inviting.set(true);
    this.inviteError.set(null);
    this.lastInvitedUser.set(null);
    try {
      const created = await this.admin.inviteUser(email, this.inviteRoleId);
      this.lastInvitedUser.set({ email: created.email, password: created.temporary_password });
      this.inviteEmail = '';
      await this.loadUsers();
    } catch {
      this.inviteError.set('admin.inviteFailed');
    } finally {
      this.inviting.set(false);
    }
  }

  protected async changeUserRole(userId: string, roleId: string): Promise<void> {
    if (!roleId) {
      return;
    }
    try {
      const updated = await this.admin.updateUserRole(userId, roleId);
      this.users.update((current) => current.map((u) => (u.id === userId ? updated : u)));
    } catch {
      this.usersError.set('admin.roleChangeFailed');
    }
  }

  protected togglePermission(permission: string): void {
    this.newRolePermissions.update((current) => {
      const next = new Set(current);
      if (next.has(permission)) {
        next.delete(permission);
      } else {
        next.add(permission);
      }
      return next;
    });
  }

  protected async createRole(): Promise<void> {
    const name = this.newRoleName.trim();
    if (!name || this.creatingRole()) {
      return;
    }

    this.creatingRole.set(true);
    this.createRoleError.set(null);
    try {
      await this.admin.createRole(name, [...this.newRolePermissions()]);
      this.newRoleName = '';
      this.newRolePermissions.set(new Set());
      await this.loadRoles();
    } catch {
      this.createRoleError.set('admin.createRoleFailed');
    } finally {
      this.creatingRole.set(false);
    }
  }
}
