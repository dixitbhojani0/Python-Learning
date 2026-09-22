import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { LoginComponent } from './login';
import { AuthService } from '../../core/auth/auth.service';

const LANGS = { en: { login: { title: 'Sign in', submit: 'Sign in', submitting: 'Signing in…', error: 'Could not sign in.' } } };

describe('LoginComponent', () => {
  let authStub: { login: ReturnType<typeof vi.fn> };

  async function setup() {
    authStub = { login: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [
        LoginComponent,
        TranslocoTestingModule.forRoot({ langs: LANGS, translocoConfig: { availableLangs: ['en'], defaultLang: 'en' } }),
      ],
      providers: [provideRouter([]), { provide: AuthService, useValue: authStub }],
    }).compileComponents();

    const fixture = TestBed.createComponent(LoginComponent);
    fixture.detectChanges();
    return fixture;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('logs in with the entered credentials and navigates to /chat on success', async () => {
    const fixture = await setup();
    authStub.login.mockResolvedValue(undefined);
    const component = fixture.componentInstance as unknown as { tenantSlug: string; email: string; password: string; submit: () => Promise<void> };
    component.tenantSlug = 'acme';
    component.email = 'a@b.com';
    component.password = 'pw';
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl');

    await component.submit();

    expect(authStub.login).toHaveBeenCalledWith('acme', 'a@b.com', 'pw');
    expect(navigateSpy).toHaveBeenCalledWith('/chat');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows a translated error and does not navigate when login fails', async () => {
    const fixture = await setup();
    authStub.login.mockRejectedValue(new Error('401'));
    const component = fixture.componentInstance as unknown as { submit: () => Promise<void> };
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigateByUrl');

    await component.submit();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="login-error"]')).toBeTruthy();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('disables the submit button while a login request is in flight', async () => {
    let resolveLogin!: () => void;
    const fixture = await setup();
    authStub.login.mockReturnValue(new Promise<void>((resolve) => (resolveLogin = resolve)));
    const component = fixture.componentInstance as unknown as { submit: () => Promise<void> };

    const submitPromise = component.submit();
    fixture.detectChanges();
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="login-submit"]')!;
    expect(button.disabled).toBe(true);

    resolveLogin();
    await submitPromise;
    fixture.detectChanges();
    expect(button.disabled).toBe(false);
  });
});
