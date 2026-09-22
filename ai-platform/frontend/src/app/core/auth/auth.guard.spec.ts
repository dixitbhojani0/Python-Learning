import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, UrlTree } from '@angular/router';
import { provideRouter } from '@angular/router';
import { authGuard } from './auth.guard';
import { AuthService } from './auth.service';

describe('authGuard', () => {
  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
  });

  afterEach(() => sessionStorage.clear());

  function runGuard(): boolean | UrlTree {
    return TestBed.runInInjectionContext(() => authGuard({} as never, {} as never)) as boolean | UrlTree;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('allows navigation when authenticated', () => {
    sessionStorage.setItem('ai-platform.access_token', 'tok');
    TestBed.inject(AuthService); // picks up the pre-existing token on construction

    expect(runGuard()).toBe(true);
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('redirects to /login when not authenticated', () => {
    const result = runGuard();

    expect(result).not.toBe(true);
    const router = TestBed.inject(Router);
    expect(router.serializeUrl(result as UrlTree)).toBe('/login');
  });
});
