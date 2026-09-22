import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

const STORAGE_KEY = 'ai-platform.access_token';

interface LoginResponse {
  access_token: string;
  token_type: string;
}

/**
 * sessionStorage, not localStorage: a dev-stage token should not silently
 * outlive the browser tab. Real OIDC/refresh-token handling is the seam
 * this whole login flow is a stand-in for (§M IdentityProviderAdapter) —
 * this is deliberately the simplest thing that lets the rest of Phase 3
 * (the chat UI) be exercised at all.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  private readonly _token = signal<string | null>(sessionStorage.getItem(STORAGE_KEY));
  readonly token = this._token.asReadonly();
  readonly isAuthenticated = computed(() => this._token() !== null);

  async login(tenantSlug: string, email: string, password: string): Promise<void> {
    const response = await firstValueFrom(
      this.http.post<LoginResponse>('/api/v1/auth/login', { tenant_slug: tenantSlug, email, password })
    );
    this._token.set(response.access_token);
    sessionStorage.setItem(STORAGE_KEY, response.access_token);
  }

  logout(): void {
    this._token.set(null);
    sessionStorage.removeItem(STORAGE_KEY);
  }
}
