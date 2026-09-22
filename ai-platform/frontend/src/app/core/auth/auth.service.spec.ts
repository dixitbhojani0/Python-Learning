import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AuthService } from './auth.service';

const STORAGE_KEY = 'ai-platform.access_token';

describe('AuthService', () => {
  let service: AuthService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(AuthService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
    sessionStorage.clear();
  });

  // ── Positive ────────────────────────────────────────────────────────────

  it('starts unauthenticated with no stored token', () => {
    expect(service.isAuthenticated()).toBe(false);
    expect(service.token()).toBeNull();
  });

  it('login() sets the token and marks the user authenticated', async () => {
    const promise = service.login('acme', 'a@b.com', 'pw');
    httpMock.expectOne('/api/v1/auth/login').flush({ access_token: 'tok-123', token_type: 'bearer' });
    await promise;

    expect(service.token()).toBe('tok-123');
    expect(service.isAuthenticated()).toBe(true);
  });

  it('login() persists the token to sessionStorage', async () => {
    const promise = service.login('acme', 'a@b.com', 'pw');
    httpMock.expectOne('/api/v1/auth/login').flush({ access_token: 'tok-456', token_type: 'bearer' });
    await promise;

    expect(sessionStorage.getItem(STORAGE_KEY)).toBe('tok-456');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('login() rejects and leaves the user unauthenticated on a failed request', async () => {
    const promise = service.login('acme', 'a@b.com', 'wrong');
    httpMock.expectOne('/api/v1/auth/login').flush('nope', { status: 401, statusText: 'Unauthorized' });

    await expect(promise).rejects.toBeTruthy();
    expect(service.isAuthenticated()).toBe(false);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('reads a pre-existing sessionStorage token on construction', () => {
    sessionStorage.setItem(STORAGE_KEY, 'pre-existing');
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });

    const freshService = TestBed.inject(AuthService);

    expect(freshService.isAuthenticated()).toBe(true);
    expect(freshService.token()).toBe('pre-existing');
  });

  // ── Side effects ────────────────────────────────────────────────────────

  it('logout() clears both the signal and sessionStorage', async () => {
    const promise = service.login('acme', 'a@b.com', 'pw');
    httpMock.expectOne('/api/v1/auth/login').flush({ access_token: 'tok-789', token_type: 'bearer' });
    await promise;

    service.logout();

    expect(service.token()).toBeNull();
    expect(service.isAuthenticated()).toBe(false);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
