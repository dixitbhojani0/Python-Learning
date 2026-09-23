import { Component, inject, signal } from '@angular/core';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, ProviderStatus, TelemetrySummary, TenantInfo } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-overview',
  imports: [TranslocoPipe],
  templateUrl: './overview.html',
})
export class AdminOverviewComponent {
  private readonly admin = inject(AdminService);

  protected readonly tenant = signal<TenantInfo | null>(null);
  protected readonly tenantError = signal<string | null>(null);
  protected readonly providers = signal<ProviderStatus | null>(null);
  protected readonly providersError = signal<string | null>(null);
  protected readonly telemetry = signal<TelemetrySummary | null>(null);
  protected readonly telemetryError = signal<string | null>(null);

  constructor() {
    // Independent try/catches, not Promise.all — a role missing one
    // permission must still see the sections it does have access to.
    this.loadTenant();
    this.loadProviders();
    this.loadTelemetry();
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

  private async loadTelemetry(): Promise<void> {
    try {
      this.telemetry.set(await this.admin.getTelemetry());
    } catch {
      this.telemetryError.set('admin.accessDenied');
    }
  }
}
