"""
backend/app/api/admin_routes.py

Phase 9: the first operator-facing surface in the whole platform — every
capability built so far (providers, RAG corpus, memory, tenancy) had zero
admin/control-plane surface before this, only the end-user chat UI (§P).

Deliberately NOT gated behind a new "admin" role/permission namespace of its
own: reuses "users:read" (viewing tenant-wide operational data) and adds
"documents:read"/"documents:write" (document corpus management) — the same
flat permission-string model as every other route (§30: no speculative RBAC
graph ahead of a second real use needing it).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.adapters.embedding.registry import EmbeddingRegistry
from backend.app.adapters.llm.registry import LLMRegistry
from backend.app.api.deps import Principal, get_scoped_session, get_tenant_config, require_permission
from backend.app.core.config import PLATFORM_DEFAULTS, PlatformConfig, resolve_config
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.core.security import hash_password
from backend.app.db.models import Chunk, Document, Role, Tenant, TelemetryEvent, User

router = APIRouter(prefix="/v1/admin", tags=["admin"])

# The complete, fixed permission-string vocabulary every route in this
# codebase actually checks (grepped from every `require_permission(...)`
# call site, not guessed) — role creation validates against this so an admin
# can never create a role referencing a permission that gates nothing.
KNOWN_PERMISSIONS = frozenset({"users:read", "users:write", "documents:read", "documents:write", "tools:approve"})


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    compliance_profile: str

    model_config = {"from_attributes": True}


class ProviderStatusOut(BaseModel):
    llm_provider: str
    embedding_provider: str
    available_llm_providers: list[str]
    available_embedding_providers: list[str]


class TenantConfigUpdateIn(BaseModel):
    # Both optional and independently settable — PATCH semantics, not PUT:
    # changing just the embedding provider must not require re-sending the
    # LLM provider too.
    llm_provider: str | None = None
    embedding_provider: str | None = None


class DocumentSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    chunk_count: int


class DocumentDetailOut(BaseModel):
    id: uuid.UUID
    title: str
    content: str
    chunk_count: int
    created_at: datetime


class AuditLogEntryOut(BaseModel):
    id: uuid.UUID
    event_type: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class TelemetrySummaryOut(BaseModel):
    total_chat_turns: int
    rag_usage_rate: float
    memory_usage_rate: float
    avg_latency_ms: float
    avg_response_word_count: float


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    permissions: list[str]

    model_config = {"from_attributes": True}


class RoleCreateIn(BaseModel):
    name: str
    permissions: list[str]


class UserManagementOut(BaseModel):
    id: uuid.UUID
    email: str
    role_id: uuid.UUID
    role_name: str
    created_at: datetime


class UserInviteIn(BaseModel):
    email: str
    role_id: uuid.UUID


class UserInviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role_id: uuid.UUID
    # Only ever shown once, in this response — there's no email/SMTP
    # infrastructure in this environment to deliver an invite link, so the
    # admin creating the account is handed the temporary password directly
    # to share out-of-band (§M: the same "mock what needs a real external
    # system" pattern as every other adapter here, not a shortcut unique to
    # this route).
    temporary_password: str


class UserRoleUpdateIn(BaseModel):
    role_id: uuid.UUID


@router.get("/tenant", response_model=TenantOut)
async def get_tenant(
    principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> TenantOut:
    # No RLS policy on `tenants` itself (it's the top of the hierarchy, §J) —
    # a tenant-scoped session can still read its own row here without issue.
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return TenantOut.model_validate(tenant)


@router.get("/providers", response_model=ProviderStatusOut)
async def get_provider_status(
    _principal: Principal = Depends(require_permission("users:read")),  # gates access; value itself unused
    config: PlatformConfig = Depends(get_tenant_config),
) -> ProviderStatusOut:
    return ProviderStatusOut(
        llm_provider=config.llm_provider,
        embedding_provider=config.embedding_provider,
        available_llm_providers=LLMRegistry.available(),
        available_embedding_providers=EmbeddingRegistry.available(),
    )


@router.patch("/config", response_model=ProviderStatusOut)
async def update_tenant_config(
    body: TenantConfigUpdateIn,
    principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> ProviderStatusOut:
    """
    Writes the tenant layer of the config hierarchy (§7) for real — the
    resolver has always supported this layer; nothing ever persisted to it
    until this endpoint existed. Validates against the actual provider
    registries before saving, the same fail-closed discipline as every other
    layer: an admin can never save an override that would make every
    subsequent chat/RAG request in this tenant start 500ing.
    """
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    if body.llm_provider is not None and body.llm_provider not in LLMRegistry.available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t(
                "admin.unknown_provider",
                locale=locale,
                kind="LLM",
                name=body.llm_provider,
                available=", ".join(LLMRegistry.available()),
            ),
        )
    if body.embedding_provider is not None and body.embedding_provider not in EmbeddingRegistry.available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t(
                "admin.unknown_provider",
                locale=locale,
                kind="embedding",
                name=body.embedding_provider,
                available=", ".join(EmbeddingRegistry.available()),
            ),
        )

    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    updates = body.model_dump(exclude_none=True)
    # JSONB columns don't track in-place mutation (SQLAlchemy sees the same
    # dict object, not a reassignment) — build a new dict so the UPDATE
    # actually fires, the same pitfall as any ORM JSON column.
    tenant.config_overrides = {**tenant.config_overrides, **updates}
    await session.flush()

    resolved = resolve_config(PLATFORM_DEFAULTS, tenant.config_overrides)
    return ProviderStatusOut(
        llm_provider=resolved.llm_provider,
        embedding_provider=resolved.embedding_provider,
        available_llm_providers=LLMRegistry.available(),
        available_embedding_providers=EmbeddingRegistry.available(),
    )


@router.get("/documents", response_model=list[DocumentSummaryOut])
async def list_documents(
    _principal: Principal = Depends(require_permission("documents:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[DocumentSummaryOut]:
    rows = (
        await session.execute(
            select(Document, func.count(Chunk.id))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [DocumentSummaryOut(id=doc.id, title=doc.title, chunk_count=count) for doc, count in rows]


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    document_id: uuid.UUID,
    _principal: Principal = Depends(require_permission("documents:read")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> DocumentDetailOut:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.document_not_found", locale=locale))

    chunk_count = (
        await session.execute(select(func.count(Chunk.id)).where(Chunk.document_id == document_id))
    ).scalar_one()

    return DocumentDetailOut(
        id=document.id,
        title=document.title,
        content=document.content,
        chunk_count=chunk_count,
        created_at=document.created_at,
    )


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    _principal: Principal = Depends(require_permission("documents:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.document_not_found", locale=locale))

    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await session.delete(document)
    return {"deleted": str(document_id)}


@router.get("/telemetry", response_model=TelemetrySummaryOut)
async def get_telemetry_summary(
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> TelemetrySummaryOut:
    # Aggregated in Python, not SQL, deliberately: at this platform's dev/test
    # scale (§ audit table) a few hundred rows is nothing, and JSONB-aggregate
    # SQL would be real complexity for no measurable benefit yet — the seam to
    # revisit is the same one named for pgvector→Qdrant: a real bottleneck, not now.
    rows = (
        await session.execute(
            select(TelemetryEvent)
            .where(TelemetryEvent.event_type == "chat_turn")
            .order_by(TelemetryEvent.created_at.desc())
            .limit(200)
        )
    ).scalars().all()

    total = len(rows)
    if total == 0:
        return TelemetrySummaryOut(
            total_chat_turns=0, rag_usage_rate=0.0, memory_usage_rate=0.0, avg_latency_ms=0.0, avg_response_word_count=0.0
        )

    rag_count = sum(1 for r in rows if r.payload.get("rag_used"))
    memory_count = sum(1 for r in rows if r.payload.get("memory_used"))
    avg_latency = sum(r.payload.get("latency_ms", 0) for r in rows) / total
    avg_words = sum(r.payload.get("response_word_count", 0) for r in rows) / total

    return TelemetrySummaryOut(
        total_chat_turns=total,
        rag_usage_rate=round(rag_count / total, 3),
        memory_usage_rate=round(memory_count / total, 3),
        avg_latency_ms=round(avg_latency, 1),
        avg_response_word_count=round(avg_words, 1),
    )


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[RoleOut]:
    roles = (await session.execute(select(Role))).scalars().all()
    return [RoleOut.model_validate(r) for r in roles]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreateIn,
    principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> RoleOut:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    unknown = set(body.permissions) - KNOWN_PERMISSIONS
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t("admin.invalid_permissions", locale=locale, permissions=", ".join(sorted(unknown))),
        )

    existing = (await session.execute(select(Role).where(Role.name == body.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=t("admin.role_name_taken", locale=locale))

    role = Role(id=uuid.uuid4(), tenant_id=principal.tenant_id, name=body.name, permissions=body.permissions)
    session.add(role)
    await session.flush()
    return RoleOut.model_validate(role)


@router.get("/users", response_model=list[UserManagementOut])
async def list_users(
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[UserManagementOut]:
    users = (
        await session.execute(select(User).options(selectinload(User.role)).order_by(User.created_at))
    ).scalars().all()
    return [
        UserManagementOut(id=u.id, email=u.email, role_id=u.role_id, role_name=u.role.name, created_at=u.created_at)
        for u in users
    ]


@router.post("/users", response_model=UserInviteOut, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: UserInviteIn,
    principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> UserInviteOut:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    # RLS already scopes this lookup to the caller's own tenant — a role_id
    # belonging to another tenant simply isn't found, the same "404, not
    # 403" non-disclosure discipline as tenant_routes.py.
    role = await session.get(Role, body.role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.role_not_found", locale=locale))

    existing = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=t("admin.email_already_in_use", locale=locale)
        )

    temporary_password = secrets.token_urlsafe(12)
    user = User(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        email=body.email,
        hashed_password=hash_password(temporary_password),
        role_id=role.id,
    )
    session.add(user)
    await session.flush()
    return UserInviteOut(id=user.id, email=user.email, role_id=user.role_id, temporary_password=temporary_password)


@router.patch("/users/{user_id}/role", response_model=UserManagementOut)
async def update_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdateIn,
    _principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> UserManagementOut:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.user_not_found", locale=locale))

    role = await session.get(Role, body.role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.role_not_found", locale=locale))

    user.role_id = role.id
    await session.flush()
    return UserManagementOut(id=user.id, email=user.email, role_id=user.role_id, role_name=role.name, created_at=user.created_at)


# The complete, real set of event types anything in this codebase actually
# records (grepped from every record_event(...) call site) — the frontend's
# category filter dropdown is built from this, not invented ahead of a real
# event type existing.
KNOWN_AUDIT_EVENT_TYPES = (
    "chat_turn",
    "tool_call",
    "tool_call_pending_approval",
    "tool_call_approved",
    "tool_call_rejected",
    "ingestion_job_completed",
    "ingestion_job_failed",
)


@router.get("/audit-log", response_model=list[AuditLogEntryOut])
async def list_audit_log(
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[AuditLogEntryOut]:
    query = select(TelemetryEvent).order_by(TelemetryEvent.created_at.desc()).limit(limit)
    if event_type:
        query = query.where(TelemetryEvent.event_type == event_type)
    events = (await session.execute(query)).scalars().all()
    return [AuditLogEntryOut.model_validate(e) for e in events]
