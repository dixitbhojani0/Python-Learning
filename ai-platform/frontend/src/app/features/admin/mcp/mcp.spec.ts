import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { AdminMcpComponent } from './mcp';
import { AdminService } from '../../../core/admin/admin.service';

const LANGS = {
  en: {
    admin: {
      mcpServers: 'MCP servers', mcpServersHint: 'hint', addMcpServer: 'Add server', addingMcpServer: 'Adding…',
      addMcpServerFailed: 'Could not add this server.', mcpServerName: 'Name', mcpServerUrl: 'Server URL',
      mcpServerAuthTokenHint: 'Bearer token (optional)', noMcpServers: 'No MCP servers connected yet.',
      mcpServerDeleteFailed: 'Could not remove this server.', enabled: 'Enabled', disabled: 'Disabled',
      testConnection: 'Test connection', testingConnection: 'Testing…', testConnectionFailed: 'Could not connect.',
      noToolsDiscovered: 'No tools.', discoveredTools: 'Discovered tools', toolToCall: 'Tool',
      toolArguments: 'Arguments (JSON)', invalidToolArguments: 'Arguments must be valid JSON.',
      callTool: 'Call tool', callingTool: 'Calling…', toolCallFailed: 'Could not call this tool.',
      accessDenied: "You don't have permission to view this section.", delete: 'Delete',
    },
  },
};

type AdminStub = {
  listMcpServers: ReturnType<typeof vi.fn>;
  createMcpServer: ReturnType<typeof vi.fn>;
  deleteMcpServer: ReturnType<typeof vi.fn>;
  testMcpServerConnection: ReturnType<typeof vi.fn>;
  callMcpServerTool: ReturnType<typeof vi.fn>;
};

describe('AdminMcpComponent', () => {
  let adminStub: AdminStub;

  async function setup(overrides: Partial<AdminStub> = {}) {
    adminStub = {
      listMcpServers: vi.fn().mockResolvedValue([]),
      createMcpServer: vi.fn(),
      deleteMcpServer: vi.fn(),
      testMcpServerConnection: vi.fn(),
      callMcpServerTool: vi.fn(),
      ...overrides,
    };

    await TestBed.configureTestingModule({
      imports: [
        AdminMcpComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: AdminService, useValue: adminStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(AdminMcpComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  const server = {
    id: 's1', name: 'My Server', url: 'http://example.local/mcp', enabled: true, created_at: '2026-01-01T00:00:00Z',
  };

  // ── Positive ────────────────────────────────────────────────────────────

  it('loads and renders servers on construction', async () => {
    const fixture = await setup({ listMcpServers: vi.fn().mockResolvedValue([server]) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="mcp-server-0"]')!.textContent).toContain('My Server');
  });

  it('adding a server clears the form and refreshes the list', async () => {
    const listMcpServers = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([server]);
    const fixture = await setup({ createMcpServer: vi.fn().mockResolvedValue(server), listMcpServers });
    const component = fixture.componentInstance as unknown as {
      name: string; url: string; authToken: string; addServer: () => Promise<void>;
    };
    component.name = 'My Server';
    component.url = 'http://example.local/mcp';
    component.authToken = 'secret';

    await component.addServer();
    fixture.detectChanges();

    expect(adminStub.createMcpServer).toHaveBeenCalledWith('My Server', 'http://example.local/mcp', 'secret');
    expect(component.name).toBe('');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="mcp-server-0"]')).toBeTruthy();
  });

  it('testing a connection shows the discovered tools', async () => {
    const tools = [{ name: 'add', description: 'Add two numbers', input_schema: {} }];
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      testMcpServerConnection: vi.fn().mockResolvedValue({ tools }),
    });
    const component = fixture.componentInstance as unknown as {
      testConnection: (s: typeof server) => Promise<void>;
      discoveredTools: import('@angular/core').WritableSignal<typeof tools>;
    };

    await component.testConnection(server);

    expect(adminStub.testMcpServerConnection).toHaveBeenCalledWith('s1');
    expect(component.discoveredTools()).toEqual(tools);
  });

  it('testing the same server again collapses the panel', async () => {
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      testMcpServerConnection: vi.fn().mockResolvedValue({ tools: [] }),
    });
    const component = fixture.componentInstance as unknown as {
      testConnection: (s: typeof server) => Promise<void>;
      expandedServerId: import('@angular/core').WritableSignal<string | null>;
    };

    await component.testConnection(server);
    expect(component.expandedServerId()).toBe('s1');
    await component.testConnection(server);
    expect(component.expandedServerId()).toBeNull();
  });

  it('calling a tool with valid JSON arguments shows the result', async () => {
    const result = { is_error: false, content: [{ type: 'text', text: '5' }] };
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      testMcpServerConnection: vi.fn().mockResolvedValue({ tools: [{ name: 'add', description: null, input_schema: {} }] }),
      callMcpServerTool: vi.fn().mockResolvedValue(result),
    });
    const component = fixture.componentInstance as unknown as {
      testConnection: (s: typeof server) => Promise<void>;
      selectedTool: string; toolArgsJson: string;
      callTool: () => Promise<void>;
      callResult: import('@angular/core').WritableSignal<typeof result | null>;
    };
    await component.testConnection(server);
    component.selectedTool = 'add';
    component.toolArgsJson = '{"a": 2, "b": 3}';

    await component.callTool();

    expect(adminStub.callMcpServerTool).toHaveBeenCalledWith('s1', 'add', { a: 2, b: 3 });
    expect(component.callResult()).toEqual(result);
  });

  it('deleting a server removes it from the list', async () => {
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      deleteMcpServer: vi.fn().mockResolvedValue({ deleted: 's1' }),
    });
    const component = fixture.componentInstance as unknown as {
      deleteServer: (id: string) => Promise<void>;
      servers: import('@angular/core').WritableSignal<(typeof server)[]>;
    };

    await component.deleteServer('s1');

    expect(component.servers()).toEqual([]);
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows accessDenied when the load fails', async () => {
    const fixture = await setup({ listMcpServers: vi.fn().mockRejectedValue(new Error('403')) });
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="mcp-servers-error"]')).toBeTruthy();
  });

  it('a failed add shows an error and keeps the typed form', async () => {
    const fixture = await setup({ createMcpServer: vi.fn().mockRejectedValue(new Error('400')) });
    const component = fixture.componentInstance as unknown as { name: string; url: string; addServer: () => Promise<void> };
    component.name = 'Keep this';
    component.url = 'http://keep.local';

    await component.addServer();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="mcp-add-error"]')).toBeTruthy();
    expect(component.name).toBe('Keep this');
  });

  it('a failed test-connection shows an error', async () => {
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      testMcpServerConnection: vi.fn().mockRejectedValue(new Error('502')),
    });
    const component = fixture.componentInstance as unknown as {
      testConnection: (s: typeof server) => Promise<void>;
      testError: import('@angular/core').WritableSignal<string | null>;
    };

    await component.testConnection(server);

    expect(component.testError()).toBe('admin.testConnectionFailed');
  });

  it('invalid JSON arguments show an error without calling the backend', async () => {
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      testMcpServerConnection: vi.fn().mockResolvedValue({ tools: [{ name: 'add', description: null, input_schema: {} }] }),
    });
    const component = fixture.componentInstance as unknown as {
      testConnection: (s: typeof server) => Promise<void>;
      selectedTool: string; toolArgsJson: string;
      callTool: () => Promise<void>;
      callError: import('@angular/core').WritableSignal<string | null>;
    };
    await component.testConnection(server);
    component.selectedTool = 'add';
    component.toolArgsJson = '{not json}';

    await component.callTool();

    expect(component.callError()).toBe('admin.invalidToolArguments');
    expect(adminStub.callMcpServerTool).not.toHaveBeenCalled();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('does nothing when adding with an empty name or url', async () => {
    const fixture = await setup();
    const component = fixture.componentInstance as unknown as { name: string; url: string; addServer: () => Promise<void> };
    component.name = '';
    component.url = 'has-url-no-name';

    await component.addServer();

    expect(adminStub.createMcpServer).not.toHaveBeenCalled();
  });

  it('does nothing when calling a tool with none selected', async () => {
    const fixture = await setup({
      listMcpServers: vi.fn().mockResolvedValue([server]),
      testMcpServerConnection: vi.fn().mockResolvedValue({ tools: [] }),
    });
    const component = fixture.componentInstance as unknown as {
      testConnection: (s: typeof server) => Promise<void>;
      callTool: () => Promise<void>;
    };
    await component.testConnection(server);

    await component.callTool();

    expect(adminStub.callMcpServerTool).not.toHaveBeenCalled();
  });

  it('shows the empty-servers message when none are connected', async () => {
    const fixture = await setup();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="mcp-servers-empty"]')).toBeTruthy();
  });
});
