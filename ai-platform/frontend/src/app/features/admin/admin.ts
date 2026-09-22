import { JsonPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import {
  AdminService,
  DocumentSummary,
  PendingApproval,
  ProviderStatus,
  TelemetrySummary,
  TenantInfo,
} from '../../core/admin/admin.service';

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

  constructor() {
    // Independent try/catches, not Promise.all — a role missing one
    // permission (e.g. documents:read but not users:read) must still see
    // the sections it *does* have access to, not a single all-or-nothing failure.
    this.loadTenant();
    this.loadProviders();
    this.loadDocuments();
    this.loadTelemetry();
    this.loadPendingApprovals();
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
}
