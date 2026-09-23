"""
backend/app/api/ingestion_routes.py

Phase 19: the real async ingestion pipeline — a job is created and scheduled
via FastAPI BackgroundTasks (see rag/pipeline.py), and this file's three
routes are how an admin creates one and watches it progress. Reuses
"documents:read"/"documents:write" (the same permissions that already gate
the synchronous `POST /v1/documents` and the Documents admin page) rather
than a new permission namespace — a job is just another way documents get
into this tenant's corpus (§30: no speculative RBAC ahead of a second real
use needing it, the same call admin_routes.py already made).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import Principal, get_scoped_session, get_tenant_config, require_permission
from backend.app.core.config import PlatformConfig
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import IngestionJob
from backend.app.db.session import tenant_scoped_session
from backend.app.rag.pipeline import run_ingestion_job

router = APIRouter(prefix="/v1/admin/ingestion-jobs", tags=["ingestion"])

# The real, complete set of stages rag/pipeline.py ever assigns — mirrors
# IngestionJob's own docstring on why there's no "parsing" or "vector store
# upsert" stage distinct from these.
KNOWN_STAGES = ("queued", "chunking", "embedding", "storing", "complete", "failed")


class IngestRequest(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)


class JobSummaryOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    title: str
    stage: str
    chunk_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobDetailOut(JobSummaryOut):
    embedding_provider: str
    error_message: str | None
    stage_log: list[dict]


@router.post("", response_model=JobSummaryOut, status_code=status.HTTP_202_ACCEPTED)
async def create_ingestion_job(
    body: IngestRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_permission("documents:write")),
    config: PlatformConfig = Depends(get_tenant_config),
) -> JobSummaryOut:
    # A real, empirically-confirmed FastAPI ordering gotcha, not a hypothetical
    # one: a `Depends(get_scoped_session)` dependency's session commits when
    # its generator is torn down, but that teardown happens AFTER this
    # function returns and the Response (with BackgroundTasks already
    # attached) has already been built — measurably after, in fact, BEFORE
    # the background task ever runs against a *different* session, so it
    # queried a row that wasn't committed yet and found nothing. Traced by
    # instrumenting _advance() directly: every lookup inside the background
    # task returned None for a job created two lines earlier in this same
    # request. Fixed the same way chat_routes.py already handles this exact
    # class of problem (see its module docstring) — an explicit,
    # self-contained `tenant_scoped_session` block that commits BEFORE
    # `background_tasks.add_task(...)` is ever reached, instead of trusting
    # dependency-cleanup timing relative to response construction.
    async with tenant_scoped_session(principal.tenant_id) as session:
        job = IngestionJob(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            title=body.title,
            embedding_provider=config.embedding_provider,
            stage="queued",
            stage_log=[],
        )
        session.add(job)
        await session.flush()
        job_id, tenant_id = job.id, principal.tenant_id
        job_out = JobSummaryOut.model_validate(job)

    background_tasks.add_task(
        run_ingestion_job, job_id, tenant_id, body.title, body.content, config.embedding_provider
    )
    return job_out


@router.get("", response_model=list[JobSummaryOut])
async def list_ingestion_jobs(
    stage: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _principal: Principal = Depends(require_permission("documents:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[JobSummaryOut]:
    query = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit)
    if stage:
        query = query.where(IngestionJob.stage == stage)
    jobs = (await session.execute(query)).scalars().all()
    return [JobSummaryOut.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_ingestion_job(
    job_id: uuid.UUID,
    _principal: Principal = Depends(require_permission("documents:read")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> JobDetailOut:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]
    job = await session.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.ingestion_job_not_found", locale=locale))
    return JobDetailOut.model_validate(job)
