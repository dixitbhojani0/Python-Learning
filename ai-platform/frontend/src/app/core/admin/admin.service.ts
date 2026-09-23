import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
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

export interface DocumentDetail {
  id: string;
  title: string;
  content: string;
  chunk_count: number;
  created_at: string;
}

export interface AuditLogEntry {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
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

export interface Role {
  id: string;
  name: string;
  permissions: string[];
}

export interface ManagedUser {
  id: string;
  email: string;
  role_id: string;
  role_name: string;
  created_at: string;
}

export interface InvitedUser {
  id: string;
  email: string;
  role_id: string;
  temporary_password: string;
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

  listRoles() {
    return firstValueFrom(this.http.get<Role[]>('/api/v1/admin/roles'));
  }

  createRole(name: string, permissions: string[]) {
    return firstValueFrom(this.http.post<Role>('/api/v1/admin/roles', { name, permissions }));
  }

  listUsers() {
    return firstValueFrom(this.http.get<ManagedUser[]>('/api/v1/admin/users'));
  }

  inviteUser(email: string, roleId: string) {
    return firstValueFrom(this.http.post<InvitedUser>('/api/v1/admin/users', { email, role_id: roleId }));
  }

  updateUserRole(userId: string, roleId: string) {
    return firstValueFrom(this.http.patch<ManagedUser>(`/api/v1/admin/users/${userId}/role`, { role_id: roleId }));
  }

  updateConfig(llmProvider: string | null, embeddingProvider: string | null) {
    const body: Record<string, string> = {};
    if (llmProvider) {
      body['llm_provider'] = llmProvider;
    }
    if (embeddingProvider) {
      body['embedding_provider'] = embeddingProvider;
    }
    return firstValueFrom(this.http.patch<ProviderStatus>('/api/v1/admin/config', body));
  }

  getDocument(id: string) {
    return firstValueFrom(this.http.get<DocumentDetail>(`/api/v1/admin/documents/${id}`));
  }

  listAuditLog(eventType: string | null) {
    let params = new HttpParams();
    if (eventType) {
      params = params.set('event_type', eventType);
    }
    return firstValueFrom(this.http.get<AuditLogEntry[]>('/api/v1/admin/audit-log', { params }));
  }
}
