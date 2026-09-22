import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';

describe('authInterceptor', () => {
  let http: HttpClient;
  let auth: AuthService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([authInterceptor])), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpClient);
    auth = TestBed.inject(AuthService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
    sessionStorage.clear();
  });

  async function loginAs(token: string) {
    const promise = auth.login('acme', 'a@b.com', 'pw');
    httpMock.expectOne('/api/v1/auth/login').flush({ access_token: token, token_type: 'bearer' });
    await promise;
  }

  // ── Positive ────────────────────────────────────────────────────────────

  it('adds the Authorization header to /api/* requests when a token exists', async () => {
    await loginAs('secret-token');

    http.get('/api/v1/users/me').subscribe();
    const req = httpMock.expectOne('/api/v1/users/me');

    expect(req.request.headers.get('Authorization')).toBe('Bearer secret-token');
    req.flush({});
  });

  // ── Negative / edge ─────────────────────────────────────────────────────

  it('adds no Authorization header when there is no token', () => {
    http.get('/api/v1/users/me').subscribe();
    const req = httpMock.expectOne('/api/v1/users/me');

    expect(req.request.headers.has('Authorization')).toBe(false);
    req.flush({});
  });

  it('does not attach the token to non-/api/ requests (e.g. the i18n loader)', async () => {
    await loginAs('secret-token');

    http.get('/i18n/en.json').subscribe();
    const req = httpMock.expectOne('/i18n/en.json');

    expect(req.request.headers.has('Authorization')).toBe(false);
    req.flush({});
  });
});
