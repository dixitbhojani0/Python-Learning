import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AuthService } from '../../core/auth/auth.service';

@Component({
  selector: 'app-login',
  imports: [FormsModule, TranslocoPipe],
  templateUrl: './login.html',
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected tenantSlug = '';
  protected email = '';
  protected password = '';

  protected readonly submitting = signal(false);
  protected readonly errorMessage = signal<string | null>(null);

  protected async submit(): Promise<void> {
    this.submitting.set(true);
    this.errorMessage.set(null);
    try {
      await this.auth.login(this.tenantSlug, this.email, this.password);
      await this.router.navigateByUrl('/chat');
    } catch {
      // Deliberately one generic message regardless of failure reason (wrong
      // tenant, wrong password, network error) — the backend already avoids
      // confirming which part was wrong (§V auth table); the UI must not
      // undo that by being more specific here.
      this.errorMessage.set('login.error');
    } finally {
      this.submitting.set(false);
    }
  }
}
