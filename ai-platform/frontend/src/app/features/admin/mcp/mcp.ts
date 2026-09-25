import { JsonPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, McpCallToolResult, McpServer, McpTool } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-mcp',
  imports: [FormsModule, TranslocoPipe, JsonPipe],
  templateUrl: './mcp.html',
})
export class AdminMcpComponent {
  private readonly admin = inject(AdminService);

  protected readonly servers = signal<McpServer[]>([]);
  protected readonly serversError = signal<string | null>(null);

  protected name = '';
  protected url = '';
  protected authToken = '';
  protected readonly adding = signal(false);
  protected readonly addError = signal<string | null>(null);

  protected readonly expandedServerId = signal<string | null>(null);
  protected readonly discoveredTools = signal<McpTool[]>([]);
  protected readonly testing = signal(false);
  protected readonly testError = signal<string | null>(null);

  protected selectedTool = '';
  protected toolArgsJson = '{}';
  protected readonly calling = signal(false);
  protected readonly callError = signal<string | null>(null);
  protected readonly callResult = signal<McpCallToolResult | null>(null);

  constructor() {
    this.loadServers();
  }

  protected async loadServers(): Promise<void> {
    this.serversError.set(null);
    try {
      this.servers.set(await this.admin.listMcpServers());
    } catch {
      this.serversError.set('admin.accessDenied');
    }
  }

  protected async addServer(): Promise<void> {
    const name = this.name.trim();
    const url = this.url.trim();
    if (!name || !url || this.adding()) {
      return;
    }

    this.adding.set(true);
    this.addError.set(null);
    try {
      await this.admin.createMcpServer(name, url, this.authToken.trim() || null);
      this.name = '';
      this.url = '';
      this.authToken = '';
      await this.loadServers();
    } catch {
      this.addError.set('admin.addMcpServerFailed');
    } finally {
      this.adding.set(false);
    }
  }

  protected async deleteServer(id: string): Promise<void> {
    try {
      await this.admin.deleteMcpServer(id);
      this.servers.update((current) => current.filter((s) => s.id !== id));
      if (this.expandedServerId() === id) {
        this.collapse();
      }
    } catch {
      this.serversError.set('admin.mcpServerDeleteFailed');
    }
  }

  protected collapse(): void {
    this.expandedServerId.set(null);
    this.discoveredTools.set([]);
    this.testError.set(null);
    this.selectedTool = '';
    this.callResult.set(null);
    this.callError.set(null);
  }

  protected async testConnection(server: McpServer): Promise<void> {
    if (this.expandedServerId() === server.id) {
      this.collapse();
      return;
    }

    this.collapse();
    this.expandedServerId.set(server.id);
    this.testing.set(true);
    try {
      const { tools } = await this.admin.testMcpServerConnection(server.id);
      this.discoveredTools.set(tools);
    } catch {
      this.testError.set('admin.testConnectionFailed');
    } finally {
      this.testing.set(false);
    }
  }

  protected async callTool(): Promise<void> {
    const serverId = this.expandedServerId();
    if (!serverId || !this.selectedTool || this.calling()) {
      return;
    }

    let args: Record<string, unknown>;
    try {
      args = this.toolArgsJson.trim() ? JSON.parse(this.toolArgsJson) : {};
    } catch {
      this.callError.set('admin.invalidToolArguments');
      return;
    }

    this.calling.set(true);
    this.callError.set(null);
    this.callResult.set(null);
    try {
      this.callResult.set(await this.admin.callMcpServerTool(serverId, this.selectedTool, args));
    } catch {
      this.callError.set('admin.toolCallFailed');
    } finally {
      this.calling.set(false);
    }
  }
}
