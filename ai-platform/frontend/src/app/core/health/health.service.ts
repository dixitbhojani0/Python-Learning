import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface HealthResponse {
  status: string;
  message: string;
}

/**
 * Thin client to the backend's /health endpoint (routed through /api in dev
 * via proxy.conf.json). Kept as its own service — never called directly from
 * a component's constructor — so it can be swapped/mocked in tests without
 * touching HealthComponent.
 */
@Injectable({ providedIn: 'root' })
export class HealthService {
  private readonly http = inject(HttpClient);

  check(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>('/api/health');
  }
}
