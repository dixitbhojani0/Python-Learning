import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { HITLRequest, HITLResponse } from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class HitlService {
  private base = environment.apiUrl;

  constructor(private http: HttpClient) {}

  approve(hitlId: string): Observable<HITLResponse> {
    const body: HITLRequest = { hitl_id: hitlId };
    return this.http.post<HITLResponse>(`${this.base}/api/hitl/approve`, body);
  }

  reject(hitlId: string): Observable<HITLResponse> {
    const body: HITLRequest = { hitl_id: hitlId };
    return this.http.post<HITLResponse>(`${this.base}/api/hitl/reject`, body);
  }
}
