import { Component, inject, signal } from '@angular/core';
import { TranslocoService, TranslocoPipe } from '@jsverse/transloco';
import { HealthService } from '../../core/health/health.service';

type HealthStatus = 'loading' | 'ok' | 'error';

@Component({
  selector: 'app-health',
  imports: [TranslocoPipe],
  templateUrl: './health.html',
})
export class HealthComponent {
  private readonly healthService = inject(HealthService);
  protected readonly transloco = inject(TranslocoService);

  protected readonly status = signal<HealthStatus>('loading');
  protected readonly backendMessage = signal('');
  protected readonly availableLangs = this.transloco.getAvailableLangs() as string[];

  constructor() {
    this.refresh();
  }

  protected refresh(): void {
    this.status.set('loading');
    this.healthService.check().subscribe({
      next: (response) => {
        this.backendMessage.set(response.message);
        this.status.set('ok');
      },
      // A failed health check is an expected, handled state — never an
      // uncaught error that reaches the console/user as a stack trace.
      error: () => {
        this.status.set('error');
      },
    });
  }

  protected setLocale(lang: string): void {
    this.transloco.setActiveLang(lang);
  }
}
