import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AdminService } from './admin.service';

describe('AdminService', () => {
  let service: AdminService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(AdminService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  // ── Positive ────────────────────────────────────────────────────────────

  it('getTenant() GETs /api/v1/admin/tenant', async () => {
    const promise = service.getTenant();
    const req = httpMock.expectOne('/api/v1/admin/tenant');
    expect(req.request.method).toBe('GET');
    req.flush({ id: 't1', name: 'Acme', slug: 'acme', compliance_profile: 'standard' });

    expect(await promise).toEqual({ id: 't1', name: 'Acme', slug: 'acme', compliance_profile: 'standard' });
  });

  it('listDocuments() GETs /api/v1/admin/documents', async () => {
    const promise = service.listDocuments();
    httpMock.expectOne('/api/v1/admin/documents').flush([{ id: 'd1', title: 'Doc', chunk_count: 3 }]);
    expect(await promise).toEqual([{ id: 'd1', title: 'Doc', chunk_count: 3 }]);
  });

  it('ingestDocument() POSTs to /api/v1/documents with title and content', async () => {
    const promise = service.ingestDocument('My title', 'My content');
    const req = httpMock.expectOne('/api/v1/documents');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ title: 'My title', content: 'My content' });
    req.flush({ id: 'd1', title: 'My title', chunk_count: 1 });

    expect(await promise).toEqual({ id: 'd1', title: 'My title', chunk_count: 1 });
  });

  it('getTelemetry() GETs /api/v1/admin/telemetry', async () => {
    const promise = service.getTelemetry();
    const stats = { total_chat_turns: 3, rag_usage_rate: 0.5, memory_usage_rate: 0.1, avg_latency_ms: 10, avg_response_word_count: 6 };
    httpMock.expectOne('/api/v1/admin/telemetry').flush(stats);
    expect(await promise).toEqual(stats);
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('getProviders() propagates an HTTP error instead of swallowing it', async () => {
    const promise = service.getProviders();
    httpMock.expectOne('/api/v1/admin/providers').flush('forbidden', { status: 403, statusText: 'Forbidden' });
    await expect(promise).rejects.toBeTruthy();
  });

  // ── Edge ────────────────────────────────────────────────────────────────

  it('deleteDocument() DELETEs the specific document id', async () => {
    const promise = service.deleteDocument('d1');
    const req = httpMock.expectOne('/api/v1/admin/documents/d1');
    expect(req.request.method).toBe('DELETE');
    req.flush({ deleted: 'd1' });
    expect(await promise).toEqual({ deleted: 'd1' });
  });

  it('listPendingApprovals() GETs /api/v1/admin/tool-approvals', async () => {
    const promise = service.listPendingApprovals();
    const approvals = [{ id: 'a1', tool_name: 'delete_all_documents', tool_args: {}, status: 'pending', created_at: '2026-01-01T00:00:00Z' }];
    httpMock.expectOne('/api/v1/admin/tool-approvals').flush(approvals);
    expect(await promise).toEqual(approvals);
  });

  it('approveToolCall() POSTs to the approve endpoint for the given id', async () => {
    const promise = service.approveToolCall('a1');
    const req = httpMock.expectOne('/api/v1/admin/tool-approvals/a1/approve');
    expect(req.request.method).toBe('POST');
    req.flush({ status: 'approved', result: { deleted_document_count: 2 } });
    expect(await promise).toEqual({ status: 'approved', result: { deleted_document_count: 2 } });
  });

  it('rejectToolCall() POSTs to the reject endpoint for the given id', async () => {
    const promise = service.rejectToolCall('a1');
    const req = httpMock.expectOne('/api/v1/admin/tool-approvals/a1/reject');
    expect(req.request.method).toBe('POST');
    req.flush({ status: 'rejected' });
    expect(await promise).toEqual({ status: 'rejected' });
  });

  // ── Side effects ────────────────────────────────────────────────────────

  it('each call is independent — one request does not receive another’s response', async () => {
    const first = service.listDocuments();
    const second = service.listDocuments();
    const reqs = httpMock.match('/api/v1/admin/documents');
    expect(reqs.length).toBe(2);
    reqs[0].flush([{ id: 'a', title: 'A', chunk_count: 1 }]);
    reqs[1].flush([{ id: 'b', title: 'B', chunk_count: 2 }]);

    expect((await first)[0].id).toBe('a');
    expect((await second)[0].id).toBe('b');
  });
});
