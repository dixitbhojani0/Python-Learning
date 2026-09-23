"""
backend/app/rag/pipeline.py

Real async ingestion, executed via FastAPI BackgroundTasks after the
triggering request has already returned (see IngestionJob's own docstring in
db/models.py for why: no Celery/Redis at this scale, same call as telemetry
avoiding a full OTel stack). Each stage transition commits its own
transaction — not one big transaction for the whole job — specifically so a
concurrent `GET /v1/admin/ingestion-jobs/{id}` while the job is still running
sees real intermediate progress, not just a final all-or-nothing state.

This deliberately does NOT reuse rag/ingestion.py's `ingest_document()` — that
function is one uninterrupted transaction by design (its own caller,
`POST /v1/documents`, needs an immediate atomic result, and stays
synchronous). Threading stage-commit checkpoints through it would compromise
that for a caller that never asked for it. Some duplication of the
chunk/embed/store steps is the honest cost of two genuinely different
execution models sharing one underlying operation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.app.adapters.embedding.registry import EmbeddingRegistry
from backend.app.db.models import Chunk, Document, IngestionJob
from backend.app.db.session import tenant_scoped_session
from backend.app.observability.telemetry import record_event
from backend.app.rag.chunking import chunk_text


async def _advance(job_id: uuid.UUID, tenant_id: uuid.UUID, *, log_stage: str, message: str, to_stage: str, **fields) -> None:
    """
    Appends one stage_log entry (for `log_stage`, the stage that just
    finished or failed) and moves the job to `to_stage`. Its own committed
    transaction — see module docstring on why that matters for a caller
    polling job status mid-run.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    async with tenant_scoped_session(tenant_id) as session:
        job = await session.get(IngestionJob, job_id)
        if job is None:
            return  # deleted mid-run; nothing left to update
        job.stage = to_stage
        for key, value in fields.items():
            setattr(job, key, value)
        job.stage_log = [*job.stage_log, {"stage": log_stage, "message": message, "at": now_iso}]


async def run_ingestion_job(job_id: uuid.UUID, tenant_id: uuid.UUID, title: str, content: str, embedding_provider: str) -> None:
    try:
        await _advance(
            job_id,
            tenant_id,
            log_stage="queued",
            message="Stage 'queued' completed successfully.",
            to_stage="chunking",
            started_at=datetime.now(timezone.utc),
        )

        # "chunking" is pure computation — no DB write. Persisting the
        # Document here (before embedding can still fail) would leave a real,
        # permanently orphaned document with zero chunks behind on any later
        # failure; the actual document only exists once "storing" succeeds.
        pieces = chunk_text(content)
        await _advance(
            job_id,
            tenant_id,
            log_stage="chunking",
            message=f"Chunking produced {len(pieces)} piece(s).",
            to_stage="embedding",
        )

        # "embedding" is also pure computation (a network call for a real
        # provider, but nothing persisted either way) until "storing" below.
        vectors: list[list[float]] = []
        if pieces:
            provider = EmbeddingRegistry.create(embedding_provider)
            vectors = await provider.embed(pieces)
        await _advance(
            job_id,
            tenant_id,
            log_stage="embedding",
            message="Embedding generation complete." if pieces else "Nothing to embed — no chunks produced.",
            to_stage="storing",
        )

        async with tenant_scoped_session(tenant_id) as session:
            document = Document(id=uuid.uuid4(), tenant_id=tenant_id, title=title, content=content)
            session.add(document)
            await session.flush()
            document_id = document.id
            for index, (piece, vector) in enumerate(zip(pieces, vectors)):
                session.add(
                    Chunk(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        document_id=document_id,
                        chunk_index=index,
                        content=piece,
                        embedding=vector,
                    )
                )
            await record_event(
                session,
                tenant_id=tenant_id,
                event_type="ingestion_job_completed",
                payload={"job_id": str(job_id), "document_id": str(document_id), "chunk_count": len(pieces)},
            )

        await _advance(
            job_id,
            tenant_id,
            log_stage="storing",
            message="Stage 'storing' completed successfully.",
            to_stage="complete",
            document_id=document_id,
            chunk_count=len(pieces),
            finished_at=datetime.now(timezone.utc),
        )
    except Exception as exc:  # noqa: BLE001 — a job must always reach a terminal
        # state; an uncaught exception in a background task is otherwise only
        # logged server-side, and the job would sit "in progress" forever from
        # the caller's point of view — the one outcome worse than a captured error.
        async with tenant_scoped_session(tenant_id) as session:
            job = await session.get(IngestionJob, job_id)
            current_stage = job.stage if job is not None else "queued"
            await record_event(
                session,
                tenant_id=tenant_id,
                event_type="ingestion_job_failed",
                payload={"job_id": str(job_id), "stage": current_stage, "error": str(exc)},
            )
        await _advance(
            job_id,
            tenant_id,
            log_stage=current_stage,
            message=f"Failed: {exc}",
            to_stage="failed",
            error_message=str(exc),
            finished_at=datetime.now(timezone.utc),
        )
