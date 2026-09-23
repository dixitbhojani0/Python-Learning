import { JsonPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, PendingApproval } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-approvals',
  imports: [TranslocoPipe, JsonPipe],
  templateUrl: './approvals.html',
})
export class AdminApprovalsComponent {
  private readonly admin = inject(AdminService);

  protected readonly pendingApprovals = signal<PendingApproval[]>([]);
  protected readonly approvalsError = signal<string | null>(null);

  constructor() {
    this.loadPendingApprovals();
  }

  private async loadPendingApprovals(): Promise<void> {
    try {
      this.pendingApprovals.set(await this.admin.listPendingApprovals());
    } catch {
      this.approvalsError.set('admin.accessDenied');
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
}
