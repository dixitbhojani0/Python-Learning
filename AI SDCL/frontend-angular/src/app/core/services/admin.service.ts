import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  StatsResponse, ChunkItem, IngestRequest, IngestResponse, SessionTurn,
  SemanticFact, ClearResult,
} from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class AdminService {
  private base = environment.apiUrl;

  constructor(private http: HttpClient) { }

  getStats(): Observable<StatsResponse> {
    return this.http.get<StatsResponse>(`${this.base}/admin/stats`);
  }

  getChunks(project = environment.defaultProject, docType = '', limit = 50, offset = 0): Observable<{ chunks: ChunkItem[], count: number }> {
    return this.http.get<{ chunks: ChunkItem[], count: number }>(
      `${this.base}/admin/chunks`,
      { params: { project, doc_type: docType, limit, offset } }
    );
  }

  triggerIngest(body: IngestRequest): Observable<IngestResponse> {
    return this.http.post<IngestResponse>(`${this.base}/admin/ingest`, body);
  }

  getSessions(project = environment.defaultProject, limit = 20): Observable<{ turns: SessionTurn[], count: number }> {
    return this.http.get<{ turns: SessionTurn[], count: number }>(
      `${this.base}/admin/sessions`,
      { params: { project, limit } }
    );
  }

  getConfig(key: string): Observable<{ key: string, config: unknown }> {
    return this.http.get<{ key: string, config: unknown }>(`${this.base}/admin/config/${key}`);
  }

  reloadConfig(): Observable<{ status: string, message: string }> {
    return this.http.post<{ status: string, message: string }>(
      `${this.base}/admin/config/reload`, {}
    );
  }

  ingestFromConfluence(project: string, spaceKey: string): Observable<{
    chunks_ingested: number;
    pages_fetched: number;
    duration_seconds: number;
    message: string;
  }> {
    return this.http.post<{
      chunks_ingested: number;
      pages_fetched: number;
      duration_seconds: number;
      message: string;
    }>(`${this.base}/admin/ingest/confluence`, { project, space_key: spaceKey });
  }

  ingestFromJira(project: string, maxTickets = 100): Observable<{
    chunks_ingested: number;
    tickets_fetched: number;
    duration_seconds: number;
    message: string;
  }> {
    return this.http.post<{
      chunks_ingested: number;
      tickets_fetched: number;
      duration_seconds: number;
      message: string;
    }>(`${this.base}/admin/ingest/jira`, { project, max_tickets: maxTickets });
  }

  getMemoryFacts(project: string, limit = 50): Observable<{ facts: SemanticFact[], count: number }> {
    return this.http.get<{ facts: SemanticFact[], count: number }>(
      `${this.base}/admin/memory`,
      { params: { project, limit } }
    );
  }

  clearMemory(project: string): Observable<{ status: string, project: string }> {
    return this.http.delete<{ status: string, project: string }>(
      `${this.base}/admin/memory`,
      { params: { project } }
    );
  }

  clearAll(project: string): Observable<ClearResult> {
    return this.http.post<ClearResult>(
      `${this.base}/admin/clear`,
      null,
      { params: { project } }
    );
  }

  // ── MCP host: outbound connections to external MCP servers ──────────────────
  listMcpServers(): Observable<McpServerListItem[]> {
    return this.http.get<McpServerListItem[]>(`${this.base}/admin/mcp-servers`);
  }

  addMcpServer(body: McpServerCreate): Observable<McpServerListItem> {
    return this.http.post<McpServerListItem>(`${this.base}/admin/mcp-servers`, body);
  }

  updateMcpServer(name: string, body: McpServerUpdate): Observable<McpServerListItem> {
    return this.http.put<McpServerListItem>(`${this.base}/admin/mcp-servers/${name}`, body);
  }

  deleteMcpServer(name: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/admin/mcp-servers/${name}`);
  }

  testMcpServer(name: string): Observable<McpServerTestResult> {
    return this.http.post<McpServerTestResult>(`${this.base}/admin/mcp-servers/${name}/test`, {});
  }

  toggleMcpServerEnabled(name: string): Observable<McpServerToggleResult> {
    return this.http.post<McpServerToggleResult>(`${this.base}/admin/mcp-servers/${name}/toggle-enabled`, {});
  }

  listMcpServerTools(name: string): Observable<McpServerToolItem[]> {
    return this.http.get<McpServerToolItem[]>(`${this.base}/admin/mcp-servers/${name}/tools`);
  }

  toggleMcpTool(server: string, tool: string): Observable<McpServerToggleResult> {
    return this.http.post<McpServerToggleResult>(
      `${this.base}/admin/mcp-servers/${server}/tools/${tool}/toggle`, {});
  }
}

// ── MCP host types — match Claude Desktop / Cursor / Antigravity standard ──────
export interface McpServerListItem {
  name: string;
  url: string;
  transport: string;
  enabled: boolean;
  disabled_tool_count: number;
  tool_count: number | null;
  status: 'connected' | 'failed' | 'disabled' | 'unknown';
  is_seed: boolean;
  error: string | null;
}

export interface McpServerToolItem {
  name: string;
  description: string;
  is_write: boolean;
  enabled: boolean;
}

export interface McpServerToggleResult {
  name: string;
  enabled: boolean;
}

export interface McpServerCreate {
  name: string;
  url: string;
  transport?: string;
  headers?: Record<string, string>;
}

export interface McpServerUpdate {
  url?: string;
  transport?: string;
  headers?: Record<string, string>;
}

export interface McpServerTestResult {
  ok: boolean;
  tool_count: number | null;
  error: string | null;
}
