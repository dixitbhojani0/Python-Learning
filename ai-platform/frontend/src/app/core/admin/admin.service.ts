import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

export interface TenantInfo {
  id: string;
  name: string;
  slug: string;
  compliance_profile: string;
}

export interface ProviderStatus {
  llm_provider: string;
  embedding_provider: string;
  available_llm_providers: string[];
  available_embedding_providers: string[];
}

export interface DocumentSummary {
  id: string;
  title: string;
  chunk_count: number;
}

export interface TelemetrySummary {
  total_chat_turns: number;
  rag_usage_rate: number;
  memory_usage_rate: number;
  avg_latency_ms: number;
  avg_response_word_count: number;
}

export interface PendingApproval {
  id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  status: string;
  created_at: string;
}

@Injectable({ providedIn: 'root' })
export class AdminService {
  private readonly http = inject(HttpClient);

  getTenant() {
    return firstValueFrom(this.http.get<TenantInfo>('/api/v1/admin/tenant'));
  }

  getProviders() {
    return firstValueFrom(this.http.get<ProviderStatus>('/api/v1/admin/providers'));
  }

  listDocuments() {
    return firstValueFrom(this.http.get<DocumentSummary[]>('/api/v1/admin/documents'));
  }

  deleteDocument(id: string) {
    return firstValueFrom(this.http.delete<{ deleted: string }>(`/api/v1/admin/documents/${id}`));
  }

  ingestDocument(title: string, content: string) {
    return firstValueFrom(
      this.http.post<{ id: string; title: string; chunk_count: number }>('/api/v1/documents', { title, content })
    );
  }

  getTelemetry() {
    return firstValueFrom(this.http.get<TelemetrySummary>('/api/v1/admin/telemetry'));
  }

  listPendingApprovals() {
    return firstValueFrom(this.http.get<PendingApproval[]>('/api/v1/admin/tool-approvals'));
  }

  approveToolCall(id: string) {
    return firstValueFrom(this.http.post<{ status: string; result: unknown }>(`/api/v1/admin/tool-approvals/${id}/approve`, {}));
  }

  rejectToolCall(id: string) {
    return firstValueFrom(this.http.post<{ status: string }>(`/api/v1/admin/tool-approvals/${id}/reject`, {}));
  }
}
