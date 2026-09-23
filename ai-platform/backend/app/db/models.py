"""
backend/app/db/models.py

Phase 2 data model: Tenant / Role / User only — the minimum needed to prove
RLS-enforced tenant isolation and RBAC end-to-end. Everything else in §28 of
the blueprint (project, environment, assistant, ...) lands in later phases;
adding those tables is additive, not a redesign of these three.

Row-Level Security: `users` and `roles` carry tenant_id and are RLS-enabled
by the Alembic migration (backend/alembic/versions/*_phase2_tenancy.py) with
a policy keyed on `current_setting('app.current_tenant_id')`. The app sets
that session variable per-request (backend/app/db/session.py) — a query that
"forgets" a tenant_id filter still cannot see another tenant's rows, because
the database enforces it, not the application code (§J of the blueprint).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, String, DateTime, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from backend.app.db.base import Base

# Must match adapters/embedding/providers/{mock,gemini}_embedding_provider.py's
# DIMENSIONS/get_dimensions() — see base.py's docstring on why changing this
# is a migration + full re-embed, not a config toggle.
EMBEDDING_DIMENSIONS = 768


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # "standard" | "healthcare" | ... — the compliance-profile switch from §K/§L.
    # Just the flag in Phase 2; the profile's actual behavior differences are Phase 11.
    compliance_profile: Mapped[str] = mapped_column(String(32), default="standard")
    # The tenant layer of the hierarchical config resolver (§7, core/config.py)
    # — a partial PlatformConfig dict merged on top of platform defaults.
    # Empty dict means "no overrides, inherit platform defaults entirely."
    config_overrides: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    # Simple permission-string list (e.g. "users:read", "users:write") —
    # deliberately not a separate Permission table yet: a full RBAC graph
    # (roles <-> permissions many-to-many with its own admin UI) is real
    # scope, but nothing in Phase 2's test scenarios needs it, and adding it
    # later is additive (§30: avoid overengineering V1).
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String))


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Opt-in, off by default (§14 consent gating) — the extract-then-update
    # pipeline (backend/app/memory/) must not run at all while this is False,
    # not merely "run but discard the result."
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=False)

    role: Mapped["Role"] = relationship()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    # RLS (§J) enforces the tenant boundary; it does NOT know about users
    # within a tenant. Every query against this table must ALSO filter by
    # user_id in application code — see api/chat_routes.py's explicit checks.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserMemory(Base):
    """
    Long-term, structured (key -> value) user memory — deliberately NOT
    vector/semantic yet (§30: the RAG chunk store already covers general
    knowledge; per-user facts are few enough that a plain key lookup is the
    right-sized tool, not a speculative upgrade to vector search ahead of
    needing it). One row per (user, key): the extract-then-update pipeline
    upserts, it never just appends duplicates.
    """

    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_memories_user_id_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class TelemetryEvent(Base):
    """
    Structured AI/LLM telemetry (§20) — deliberately a plain table, not an
    OpenTelemetry collector + exporter pipeline: there is no consumer
    (Jaeger/Grafana/etc.) running in this environment for OTel spans to go
    to, so standing one up now would be infrastructure with nothing reading
    it. This is the honest scope: queryable structured events, the same
    seam an OTel exporter would replace later without changing callers
    (record_event() is the interface, not this table's schema).
    """

    __tablename__ = "telemetry_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class PendingToolApproval(Base):
    """
    §15's HITL gate for risky agent actions, made real: a high-risk tool
    call is never executed inline — it lands here as "pending" and an
    operator (an admin, via api/tool_approval_routes.py) must explicitly
    approve or reject it before backend/app/tools/ actually runs it. This
    table IS the pause-and-resume state; there is no in-memory "waiting"
    request — the original chat turn returns immediately with an
    "approval_required" event, and approval happens as a fully separate
    request, possibly minutes later, possibly from a different device.
    """

    __tablename__ = "pending_tool_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_args: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # "pending" | "approved" | "rejected"
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
