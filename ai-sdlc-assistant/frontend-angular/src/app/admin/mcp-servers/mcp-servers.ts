import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatCardModule } from '@angular/material/card';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatDividerModule } from '@angular/material/divider';
import { MatSnackBar } from '@angular/material/snack-bar';

import {
  AdminService,
  McpServerListItem,
  McpServerCreate,
  McpServerToolItem,
} from '../../core/services/admin.service';

@Component({
  selector: 'app-mcp-servers',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatTableModule, MatButtonModule, MatInputModule, MatFormFieldModule,
    MatSelectModule, MatProgressSpinnerModule, MatIconModule,
    MatChipsModule, MatTooltipModule, MatCardModule,
    MatSidenavModule, MatSlideToggleModule, MatDividerModule,
  ],
  templateUrl: './mcp-servers.html',
})
export class McpServers implements OnInit {
  servers = signal<McpServerListItem[]>([]);
  loading = signal(false);
  saving  = signal(false);
  testing = signal<Record<string, boolean>>({});
  togglingServer = signal<Record<string, boolean>>({});

  // Drawer state (right-side, shows tools for the selected server)
  drawerOpen   = signal(false);
  drawerServer = signal<McpServerListItem | null>(null);
  drawerTools  = signal<McpServerToolItem[]>([]);
  drawerLoading = signal(false);
  togglingTool = signal<Record<string, boolean>>({});

  showAddForm = signal(false);

  form = {
    name: '',
    url: '',
    transport: 'streamable_http',
    authorization: '',
  };

  displayedColumns = ['name', 'url', 'transport', 'enabled', 'status', 'tool_count', 'actions'];

  constructor(private admin: AdminService, private snack: MatSnackBar) {}

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading.set(true);
    this.admin.listMcpServers().subscribe({
      next:  (rows) => { this.servers.set(rows); this.loading.set(false); },
      error: (e)    => { this.loading.set(false); this.snack.open(`Load failed: ${e?.error?.detail || e.message}`, 'OK', { duration: 5000 }); },
    });
  }

  toggleAddForm(): void {
    this.showAddForm.update(v => !v);
    if (!this.showAddForm()) this.resetForm();
  }

  resetForm(): void {
    this.form = { name: '', url: '', transport: 'streamable_http', authorization: '' };
  }

  submitAdd(): void {
    if (!this.form.name.trim() || !this.form.url.trim()) {
      this.snack.open('Name and URL are required', 'OK', { duration: 3000 });
      return;
    }
    const body: McpServerCreate = {
      name: this.form.name.trim(),
      url: this.form.url.trim(),
      transport: this.form.transport || 'streamable_http',
    };
    const auth = this.form.authorization.trim();
    if (auth) body.headers = { Authorization: auth };

    this.saving.set(true);
    this.admin.addMcpServer(body).subscribe({
      next: (row) => {
        this.saving.set(false);
        this.snack.open(`Added '${row.name}' — ${row.status} (${row.tool_count ?? 0} tools)`, 'OK', { duration: 4000 });
        this.resetForm();
        this.showAddForm.set(false);
        this.load();
      },
      error: (e) => {
        this.saving.set(false);
        this.snack.open(`Add failed: ${e?.error?.detail || e.message}`, 'OK', { duration: 6000 });
      },
    });
  }

  testServer(name: string): void {
    this.testing.update(t => ({ ...t, [name]: true }));
    this.admin.testMcpServer(name).subscribe({
      next: (r) => {
        this.testing.update(t => ({ ...t, [name]: false }));
        const msg = r.ok ? `'${name}' OK — ${r.tool_count} tools` : `'${name}' FAILED — ${r.error}`;
        this.snack.open(msg, 'OK', { duration: 5000 });
        this.load();
      },
      error: (e) => {
        this.testing.update(t => ({ ...t, [name]: false }));
        this.snack.open(`Test failed: ${e?.error?.detail || e.message}`, 'OK', { duration: 5000 });
      },
    });
  }

  toggleServerEnabled(row: McpServerListItem): void {
    // Warn before disabling the seed — agents lose live MCP data and write actions.
    if (row.is_seed && row.enabled) {
      const ok = confirm(
        `Disable the built-in 'sdlc' MCP connection?\n\n` +
        `Agents will fall back to RAG-only responses:\n` +
        `  • No live Jira / GitHub / Slack / Confluence data\n` +
        `  • No ticket creation, no reviewer assignment, no Slack notifications\n` +
        `  • Cross-source / risk / release answers limited to ingested docs\n\n` +
        `You can re-enable it any time from this same screen.`,
      );
      if (!ok) return;
    }
    this.togglingServer.update(t => ({ ...t, [row.name]: true }));
    this.admin.toggleMcpServerEnabled(row.name).subscribe({
      next: (r) => {
        this.togglingServer.update(t => ({ ...t, [row.name]: false }));
        this.snack.open(`'${row.name}' ${r.enabled ? 'enabled' : 'disabled'}`, 'OK', { duration: 3000 });
        this.load();
      },
      error: (e) => {
        this.togglingServer.update(t => ({ ...t, [row.name]: false }));
        this.snack.open(`Toggle failed: ${e?.error?.detail || e.message}`, 'OK', { duration: 5000 });
      },
    });
  }

  deleteServer(row: McpServerListItem): void {
    if (row.is_seed) {
      // Seed has no hard-delete (url comes from env). Route through the same
      // toggle path which surfaces the RAG-only warning.
      this.toggleServerEnabled(row);
      return;
    }
    if (!confirm(`Delete MCP server '${row.name}'? This removes the outbound connection (it does not affect the remote server itself).`)) return;
    this.admin.deleteMcpServer(row.name).subscribe({
      next: () => { this.snack.open(`Deleted '${row.name}'`, 'OK', { duration: 3000 }); this.load(); },
      error: (e) => this.snack.open(`Delete failed: ${e?.error?.detail || e.message}`, 'OK', { duration: 5000 }),
    });
  }

  // ── Drawer (right side): per-server tool list with on/off toggles ───────────

  openDrawer(row: McpServerListItem): void {
    this.drawerServer.set(row);
    this.drawerOpen.set(true);
    this.loadDrawerTools(row.name);
  }

  closeDrawer(): void {
    this.drawerOpen.set(false);
    this.drawerServer.set(null);
    this.drawerTools.set([]);
  }

  loadDrawerTools(name: string): void {
    this.drawerLoading.set(true);
    this.admin.listMcpServerTools(name).subscribe({
      next:  (tools) => { this.drawerTools.set(tools); this.drawerLoading.set(false); },
      error: (e)     => {
        this.drawerLoading.set(false);
        this.snack.open(`Could not list tools: ${e?.error?.detail || e.message}`, 'OK', { duration: 5000 });
      },
    });
  }

  toggleTool(tool: McpServerToolItem): void {
    const server = this.drawerServer();
    if (!server) return;
    const key = `${server.name}:${tool.name}`;
    this.togglingTool.update(t => ({ ...t, [key]: true }));
    this.admin.toggleMcpTool(server.name, tool.name).subscribe({
      next: (r) => {
        this.togglingTool.update(t => ({ ...t, [key]: false }));
        // Optimistic update — refresh both drawer + table to keep counts honest.
        this.drawerTools.update(arr => arr.map(t => t.name === tool.name ? { ...t, enabled: r.enabled } : t));
        this.load();
      },
      error: (e) => {
        this.togglingTool.update(t => ({ ...t, [key]: false }));
        this.snack.open(`Tool toggle failed: ${e?.error?.detail || e.message}`, 'OK', { duration: 5000 });
      },
    });
  }

  statusColor(status: string): 'primary' | 'warn' | undefined {
    if (status === 'connected') return 'primary';
    if (status === 'failed')    return 'warn';
    return undefined;
  }
}
