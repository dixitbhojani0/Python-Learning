import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { App } from './app';
import { AuthService } from './core/auth/auth.service';

const LANGS = {
  en: {
    app: { brandName: 'AI Platform' },
    chat: { title: 'Chat', logout: 'Sign out' },
    admin: {
      title: 'Admin', overview: 'Overview', documents: 'Knowledge base', team: 'Team & roles',
      pendingApprovals: 'Pending tool approvals', auditLog: 'Audit & logs',
    },
  },
};

describe('App', () => {
  let authStub: { isAuthenticated: () => boolean; logout: ReturnType<typeof vi.fn> };

  async function setup(isAuthenticated: boolean) {
    authStub = { isAuthenticated: () => isAuthenticated, logout: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [
        App,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: AuthService, useValue: authStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('should create the app shell', async () => {
    const fixture = await setup(false);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('renders a router outlet so routed features can mount', async () => {
    const fixture = await setup(false);
    expect((fixture.nativeElement as HTMLElement).querySelector('router-outlet')).toBeTruthy();
  });

  it('shows the sidebar nav when authenticated', async () => {
    const fixture = await setup(true);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="nav-chat"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-overview"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-documents"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-team"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-approvals"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-audit"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="nav-logout"]')).toBeTruthy();
  });

  it('logout() clears the session and navigates to /login', async () => {
    const fixture = await setup(true);
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl');

    (fixture.componentInstance as unknown as { logout: () => void }).logout();

    expect(authStub.logout).toHaveBeenCalled();
    expect(navigateSpy).toHaveBeenCalledWith('/login');
  });

  // ── Negative / edge ────────────────────────────────────────────────────

  it('hides the sidebar nav when not authenticated', async () => {
    const fixture = await setup(false);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="nav-chat"]')).toBeNull();
    expect(el.querySelector('[data-testid="nav-overview"]')).toBeNull();
    expect(el.querySelector('[data-testid="nav-logout"]')).toBeNull();
  });
});
