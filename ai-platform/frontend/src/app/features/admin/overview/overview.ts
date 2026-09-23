import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, ProviderStatus, TelemetrySummary, TenantInfo } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-overview',
  imports: [FormsModule, TranslocoPipe],
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

  // The pending, user-editable selection — separate from `providers()` (the
  // last value actually confirmed by the server) so a failed save doesn't
  // silently revert what the admin sees selected in the dropdowns.
  protected selectedLlmProvider = '';
  protected selectedEmbeddingProvider = '';
  protected readonly savingConfig = signal(false);
  protected readonly configError = signal<string | null>(null);
  protected readonly configSaved = signal(false);

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
      const status = await this.admin.getProviders();
      this.providers.set(status);
      this.selectedLlmProvider = status.llm_provider;
      this.selectedEmbeddingProvider = status.embedding_provider;
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

  protected async saveConfig(): Promise<void> {
    if (this.savingConfig()) {
      return;
    }
    this.savingConfig.set(true);
    this.configError.set(null);
    this.configSaved.set(false);
    try {
      const updated = await this.admin.updateConfig(this.selectedLlmProvider, this.selectedEmbeddingProvider);
      this.providers.update((current) => (current ? { ...current, ...updated } : current));
      this.configSaved.set(true);
    } catch {
      this.configError.set('admin.configSaveFailed');
    } finally {
      this.savingConfig.set(false);
    }
  }
}
