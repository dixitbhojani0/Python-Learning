import { JsonPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import {
  AdminService,
  DocumentSummary,
  ManagedUser,
  PendingApproval,
  ProviderStatus,
  Role,
  TelemetrySummary,
  TenantInfo,
} from '../../core/admin/admin.service';

// Mirrors backend/app/api/admin_routes.py's KNOWN_PERMISSIONS exactly — the
// fixed vocabulary of permission strings any route in this codebase actually
// checks. Kept as a plain constant, not fetched from the backend, because it
// changes only when a new permission-gated route is added to the backend
// itself (a code change on both sides already, not independent drift).
const KNOWN_PERMISSIONS = ['users:read', 'users:write', 'documents:read', 'documents:write', 'tools:approve'] as const;

@Component({
  selector: 'app-admin',
  imports: [FormsModule, TranslocoPipe, RouterLink, JsonPipe],
  templateUrl: './admin.html',
})
export class AdminComponent {
  private readonly admin = inject(AdminService);

  protected readonly tenant = signal<TenantInfo | null>(null);
  protected readonly tenantError = signal<string | null>(null);
  protected readonly providers = signal<ProviderStatus | null>(null);
  protected readonly providersError = signal<string | null>(null);
  protected readonly documents = signal<DocumentSummary[]>([]);
  protected readonly documentsError = signal<string | null>(null);
  protected readonly telemetry = signal<TelemetrySummary | null>(null);
  protected readonly telemetryError = signal<string | null>(null);
  protected readonly pendingApprovals = signal<PendingApproval[]>([]);
  protected readonly approvalsError = signal<string | null>(null);

  protected ingestTitle = '';
  protected ingestContent = '';
  protected readonly ingesting = signal(false);
  protected readonly ingestError = signal<string | null>(null);

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
    // Independent try/catches, not Promise.all — a role missing one
    // permission (e.g. documents:read but not users:read) must still see
    // the sections it *does* have access to, not a single all-or-nothing failure.
    this.loadTenant();
    this.loadProviders();
    this.loadDocuments();
    this.loadTelemetry();
    this.loadPendingApprovals();
    this.loadUsers();
    this.loadRoles();
  }

  private async loadTenant(): Promise<void> {
    try {
      this.tenant.set(await this.admin.getTenant());
    } catch {
      this.tenantError.set('admin.accessDenied');
    }
  }

  private async loadProviders(): Promise<void> {
    try {
      this.providers.set(await this.admin.getProviders());
    } catch {
      this.providersError.set('admin.accessDenied');
    }
  }

  private async loadDocuments(): Promise<void> {
    try {
      this.documents.set(await this.admin.listDocuments());
    } catch {
      this.documentsError.set('admin.accessDenied');
    }
  }

  private async loadTelemetry(): Promise<void> {
    try {
      this.telemetry.set(await this.admin.getTelemetry());
    } catch {
      this.telemetryError.set('admin.accessDenied');
    }
  }

  private async loadPendingApprovals(): Promise<void> {
    try {
      this.pendingApprovals.set(await this.admin.listPendingApprovals());
    } catch {
      this.approvalsError.set('admin.accessDenied');
    }
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

  protected async deleteDocument(id: string): Promise<void> {
    try {
      await this.admin.deleteDocument(id);
      this.documents.update((current) => current.filter((doc) => doc.id !== id));
    } catch {
      this.documentsError.set('admin.deleteFailed');
    }
  }

  protected async approveToolCall(id: string): Promise<void> {
    try {
      await this.admin.approveToolCall(id);
      this.pendingApprovals.update((current) => current.filter((a) => a.id !== id));
    } catch {
      this.approvalsError.set('admin.approvalActionFailed');
    }
  }

  protected async rejectToolCall(id: string): Promise<void> {
    try {
      await this.admin.rejectToolCall(id);
      this.pendingApprovals.update((current) => current.filter((a) => a.id !== id));
    } catch {
      this.approvalsError.set('admin.approvalActionFailed');
    }
  }

  protected async ingest(): Promise<void> {
    const title = this.ingestTitle.trim();
    const content = this.ingestContent.trim();
    if (!title || !content || this.ingesting()) {
      return;
    }

    this.ingesting.set(true);
    this.ingestError.set(null);
    try {
      await this.admin.ingestDocument(title, content);
      this.ingestTitle = '';
      this.ingestContent = '';
      await this.loadDocuments();
    } catch {
      this.ingestError.set('admin.ingestFailed');
    } finally {
      this.ingesting.set(false);
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
