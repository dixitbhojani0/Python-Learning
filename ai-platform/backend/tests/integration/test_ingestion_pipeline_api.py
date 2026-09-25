"""
Phase 19: real async ingestion via FastAPI BackgroundTasks (rag/pipeline.py).

A genuine ordering bug was found and fixed while building this: the route
originally created the job through `Depends(get_scoped_session)`, whose
commit-on-cleanup runs AFTER the Response (with BackgroundTasks already
attached) is constructed — so the background task's own, separate session
queried a row that had not been committed yet and silently found nothing
every time. Confirmed by instrumenting the update helper directly before
fixing it, not assumed. Fixed with an explicit `tenant_scoped_session` block
in the route (the same pattern chat_routes.py already uses for the same
class of problem). These tests exercise the real, fixed behavior — with
BackgroundTasks under `httpx.ASGITransport`, the scheduled task has already
run to completion by the time a request's response is returned to the
caller, so no polling/sleeping is needed to observe a finished job.
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from backend.app.main import app

_TRANSPORT = ASGITransport(app=app)


async def _client() -> AsyncClient:
    return AsyncClient(transport=_TRANSPORT, base_url="http://testserver")


async def _login(client: AsyncClient, slug: str, email: str, password: str) -> str:
    response = await client.post("/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Positive ──────────────────────────────────────────────────────────────

async def test_creating_a_job_returns_202_with_queued_stage(make_tenant):
    tenant = await make_tenant(permissions=["documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "Doc A", "content": "hello world"}, headers=_auth_header(token)
        )

    assert response.status_code == 202
    body = response.json()
    assert body["stage"] == "queued"
    assert body["title"] == "Doc A"


async def test_a_completed_job_progresses_through_every_real_stage_in_order(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "Doc A", "content": "hello world"}, headers=_auth_header(token)
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))

    body = detail.json()
    assert body["stage"] == "complete"
    assert body["document_id"] is not None
    assert body["chunk_count"] == 1
    assert body["started_at"] is not None
    assert body["finished_at"] is not None
    assert [entry["stage"] for entry in body["stage_log"]] == ["queued", "parsing", "chunking", "embedding", "storing"]


async def test_a_completed_jobs_document_is_actually_searchable(make_tenant):
    tenant = await make_tenant(permissions=["documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.post(
            "/v1/admin/ingestion-jobs",
            json={"title": "Doc A", "content": "the quarterly revenue figures for the coffee division"},
            headers=_auth_header(token),
        )
        search = await client.get(
            "/v1/search", params={"q": "the quarterly revenue figures for the coffee division"}, headers=_auth_header(token)
        )

    assert len(search.json()) == 1  # the background job's chunks are real, queryable rows, not a UI fiction


async def test_a_completed_job_is_recorded_in_the_audit_log(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "Doc A", "content": "hello"}, headers=_auth_header(token)
        )
        audit = await client.get("/v1/admin/audit-log", headers=_auth_header(token))

    events = audit.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "ingestion_job_completed"
    assert events[0]["payload"]["chunk_count"] == 1


async def test_list_ingestion_jobs_returns_newest_first(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.post("/v1/admin/ingestion-jobs", json={"title": "First", "content": "a"}, headers=_auth_header(token))
        await client.post("/v1/admin/ingestion-jobs", json={"title": "Second", "content": "b"}, headers=_auth_header(token))

        response = await client.get("/v1/admin/ingestion-jobs", headers=_auth_header(token))

    titles = [j["title"] for j in response.json()]
    assert titles == ["Second", "First"]


async def test_list_ingestion_jobs_filters_by_stage(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.post("/v1/admin/ingestion-jobs", json={"title": "Good", "content": "hello"}, headers=_auth_header(token))
        await client.patch("/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token))
        await client.post("/v1/admin/ingestion-jobs", json={"title": "Bad", "content": "hello"}, headers=_auth_header(token))

        complete_only = await client.get(
            "/v1/admin/ingestion-jobs", params={"stage": "complete"}, headers=_auth_header(token)
        )
        failed_only = await client.get("/v1/admin/ingestion-jobs", params={"stage": "failed"}, headers=_auth_header(token))

    assert [j["title"] for j in complete_only.json()] == ["Good"]
    assert [j["title"] for j in failed_only.json()] == ["Bad"]


# ── Negative ──────────────────────────────────────────────────────────────

async def test_a_failed_job_records_the_error_and_never_creates_a_document(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        # No GEMINI_API_KEY is configured anywhere in this test environment
        # (by design) — GeminiEmbeddingProvider raises ProviderMisconfiguredError
        # immediately, before any network call, giving a deterministic failure.
        await client.patch("/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token))
        create = await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "Doomed", "content": "hello"}, headers=_auth_header(token)
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))
        documents = await client.get("/v1/admin/documents", headers=_auth_header(token))

    body = detail.json()
    assert body["stage"] == "failed"
    assert "GEMINI_API_KEY" in body["error_message"]
    assert documents.json() == []  # the Document row from "chunking" must not survive a later-stage failure


async def test_a_failed_job_is_recorded_in_the_audit_log(make_tenant):
    tenant = await make_tenant(permissions=["users:read", "documents:write", "users:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        await client.patch("/v1/admin/config", json={"embedding_provider": "gemini"}, headers=_auth_header(token))
        await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "Doomed", "content": "hello"}, headers=_auth_header(token)
        )
        audit = await client.get("/v1/admin/audit-log", headers=_auth_header(token))

    events = [e for e in audit.json() if e["event_type"] == "ingestion_job_failed"]
    assert len(events) == 1
    assert "GEMINI_API_KEY" in events[0]["payload"]["error"]


async def test_create_ingestion_job_without_documents_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["documents:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "x", "content": "y"}, headers=_auth_header(token)
        )
    assert response.status_code == 403


async def test_list_ingestion_jobs_without_documents_read_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=[])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get("/v1/admin/ingestion-jobs", headers=_auth_header(token))
    assert response.status_code == 403


async def test_get_nonexistent_ingestion_job_is_404(make_tenant):
    tenant = await make_tenant(permissions=["documents:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.get(
            "/v1/admin/ingestion-jobs/00000000-0000-0000-0000-000000000000", headers=_auth_header(token)
        )
    assert response.status_code == 404


async def test_ingestion_endpoints_require_auth():
    async with await _client() as client:
        assert (await client.get("/v1/admin/ingestion-jobs")).status_code == 401
        assert (await client.post("/v1/admin/ingestion-jobs", json={"title": "x", "content": "y"})).status_code == 401


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_content_that_chunks_to_nothing_still_completes_with_zero_chunks(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "Blank", "content": " "}, headers=_auth_header(token)
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))

    body = detail.json()
    assert body["stage"] == "complete"
    assert body["chunk_count"] == 0


# ── Side effects (tenant isolation) ──────────────────────────────────────

async def test_ingestion_jobs_never_show_another_tenants_jobs(make_tenant):
    tenant_a = await make_tenant(permissions=["documents:write"])
    tenant_b = await make_tenant(permissions=["documents:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        await client.post("/v1/admin/ingestion-jobs", json={"title": "A's job", "content": "x"}, headers=_auth_header(token_a))

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.get("/v1/admin/ingestion-jobs", headers=_auth_header(token_b))

    assert response.json() == []


async def test_cannot_get_another_tenants_job_by_id(make_tenant):
    tenant_a = await make_tenant(permissions=["documents:write"])
    tenant_b = await make_tenant(permissions=["documents:read"])

    async with await _client() as client:
        token_a = await _login(client, tenant_a.slug, tenant_a.admin_email, tenant_a.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs", json={"title": "A's job", "content": "x"}, headers=_auth_header(token_a)
        )
        job_id = create.json()["id"]

        token_b = await _login(client, tenant_b.slug, tenant_b.admin_email, tenant_b.admin_password)
        response = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token_b))

    assert response.status_code == 404


# ── File upload (Phase 20) ───────────────────────────────────────────────
# "Is this really RAG without file upload?" was a fair question — these
# exercise real files through the real parser registry (rag/parsers/), not
# just the pre-existing paste-text path.

def _make_pdf_bytes(text: str) -> bytes:
    """
    A minimal, hand-built (but genuinely valid) single-page PDF 1.4 file with
    one text-showing content stream — verified separately to actually
    round-trip through pypdf's PdfReader.extract_text() before use here.
    Not built via PdfWriter: pypdf has no high-level "draw this text" API on
    a blank page, and a first attempt using its low-level ContentStream
    object directly produced a PDF pypdf itself couldn't extract text back
    out of. Real PDF structure (objects, xref table, trailer) written
    directly is more reliable for a small, fixed test fixture than fighting
    a writer API that isn't meant for this.
    """
    import io

    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream",
    ]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode("latin-1"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_offset = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("latin-1"))
    return out.getvalue()


def _make_docx_bytes(text: str) -> bytes:
    import io
    import docx

    document = docx.Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def test_uploading_a_txt_file_ingests_its_real_content(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("notes.txt", b"the quarterly revenue figures for the coffee division", "text/plain")},
            headers=_auth_header(token),
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))
        search = await client.get(
            "/v1/search", params={"q": "the quarterly revenue figures for the coffee division"}, headers=_auth_header(token)
        )

    assert create.status_code == 202
    assert detail.json()["stage"] == "complete"
    assert len(search.json()) == 1  # the uploaded file's real, extracted text is searchable


async def test_upload_without_a_title_defaults_to_the_filename(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("Employee Handbook.txt", b"hello", "text/plain")},
            headers=_auth_header(token),
        )

    assert create.json()["title"] == "Employee Handbook"


async def test_uploading_a_docx_file_extracts_its_paragraph_text(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={
                "file": (
                    "policy.docx",
                    _make_docx_bytes("remote work is allowed on fridays for the engineering team"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=_auth_header(token),
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))

    assert detail.json()["stage"] == "complete"
    assert detail.json()["chunk_count"] == 1


async def test_uploading_a_pdf_file_extracts_its_text(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("policy.pdf", _make_pdf_bytes("expense reports are due monthly"), "application/pdf")},
            headers=_auth_header(token),
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))

    assert detail.json()["stage"] == "complete"
    assert detail.json()["chunk_count"] == 1


async def test_uploading_an_unsupported_extension_is_400_before_any_job_is_created(make_tenant):
    tenant = await make_tenant(permissions=["documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("virus.exe", b"binary junk", "application/octet-stream")},
            headers=_auth_header(token),
        )
    assert response.status_code == 400


async def test_uploading_a_corrupt_pdf_fails_the_job_cleanly_not_the_request(make_tenant):
    tenant = await make_tenant(permissions=["documents:read", "documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        create = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("broken.pdf", b"this is not a real pdf file at all", "application/pdf")},
            headers=_auth_header(token),
        )
        job_id = create.json()["id"]
        detail = await client.get(f"/v1/admin/ingestion-jobs/{job_id}", headers=_auth_header(token))

    # The upload request itself succeeds (202) — a corrupt file is a job
    # failure, discovered during "parsing", not a request-level error.
    assert create.status_code == 202
    body = detail.json()
    assert body["stage"] == "failed"
    assert body["error_message"]


async def test_upload_without_documents_write_permission_is_403(make_tenant):
    tenant = await make_tenant(permissions=["documents:read"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_auth_header(token),
        )
    assert response.status_code == 403


async def test_upload_endpoint_requires_auth():
    async with await _client() as client:
        response = await client.post(
            "/v1/admin/ingestion-jobs/upload", files={"file": ("notes.txt", b"hello", "text/plain")}
        )
    assert response.status_code == 401


async def test_upload_larger_than_the_configured_limit_is_400(make_tenant, monkeypatch):
    from backend.app.api import ingestion_routes

    monkeypatch.setattr(ingestion_routes, "_MAX_UPLOAD_BYTES", 10)  # tiny limit, no need to build a real 20MB file
    tenant = await make_tenant(permissions=["documents:write"])
    async with await _client() as client:
        token = await _login(client, tenant.slug, tenant.admin_email, tenant.admin_password)
        response = await client.post(
            "/v1/admin/ingestion-jobs/upload",
            files={"file": ("notes.txt", b"this text is longer than ten bytes", "text/plain")},
            headers=_auth_header(token),
        )
    assert response.status_code == 400
