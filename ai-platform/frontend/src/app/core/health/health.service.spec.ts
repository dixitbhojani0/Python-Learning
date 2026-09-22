import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HealthService } from './health.service';

describe('HealthService', () => {
  let service: HealthService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(HealthService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  // ── Positive ────────────────────────────────────────────────────────────

  it('GETs /api/health and returns the parsed response', () => {
    let result: unknown;
    service.check().subscribe((r) => (result = r));

    const req = httpMock.expectOne('/api/health');
    expect(req.request.method).toBe('GET');
    req.flush({ status: 'ok', message: 'Service is healthy.' });

    expect(result).toEqual({ status: 'ok', message: 'Service is healthy.' });
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('propagates an HTTP error to the caller instead of swallowing it', () => {
    let errorSeen: unknown;
    service.check().subscribe({ error: (e) => (errorSeen = e) });

    httpMock.expectOne('/api/health').flush('down', { status: 503, statusText: 'Service Unavailable' });

    expect(errorSeen).toBeTruthy();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('sends no request body on a GET', () => {
    service.check().subscribe();
    const req = httpMock.expectOne('/api/health');
    expect(req.request.body).toBeNull();
    req.flush({ status: 'ok', message: 'ok' });
  });

  // ── Side effects ────────────────────────────────────────────────────────

  it('each call() is independent — one subscriber does not receive another call’s response', () => {
    let first: unknown;
    let second: unknown;
    service.check().subscribe((r) => (first = r));
    service.check().subscribe((r) => (second = r));

    const reqs = httpMock.match('/api/health');
    expect(reqs.length).toBe(2);
    reqs[0].flush({ status: 'ok', message: 'first' });
    reqs[1].flush({ status: 'ok', message: 'second' });

    expect((first as { message: string }).message).toBe('first');
    expect((second as { message: string }).message).toBe('second');
  });
});
