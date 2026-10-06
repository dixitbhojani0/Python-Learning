import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { HITLRequest, HITLResponse } from '../models/api.models';

// E8 decision matrix (REDESIGN_BUGS.md) — only low-risk, reversible-ish action
// types may be auto-approved for the rest of a conversation. Everything else
// (ticket create/edit, reviewer assign, PR approve/reject, release GO) always
// asks, every time.
const APPROVE_ALL_ELIGIBLE = new Set<string>(['send_slack']);

@Injectable({ providedIn: 'root' })
export class HitlService {
  private base = environment.apiUrl;

  // Session-scoped (this browser tab's conversation only) — intentionally not
  // persisted, so a reload or a new session asks again. See auto-sdlc/designs/E8.md.
  private autoApproved = new Set<string>();

  constructor(private http: HttpClient) {}

  approve(hitlId: string, remember = false): Observable<HITLResponse> {
    const body: HITLRequest = { hitl_id: hitlId, remember };
    return this.http.post<HITLResponse>(`${this.base}/api/hitl/approve`, body);
  }

  reject(hitlId: string): Observable<HITLResponse> {
    const body: HITLRequest = { hitl_id: hitlId };
    return this.http.post<HITLResponse>(`${this.base}/api/hitl/reject`, body);
  }

  isApproveAllEligible(actionType: string): boolean {
    return APPROVE_ALL_ELIGIBLE.has(actionType);
  }

  isAutoApproved(actionType: string): boolean {
    return this.autoApproved.has(actionType);
  }

  markAutoApproved(actionType: string): void {
    this.autoApproved.add(actionType);
  }
}
