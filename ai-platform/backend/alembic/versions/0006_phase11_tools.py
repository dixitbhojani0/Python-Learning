"""Phase 11: pending_tool_approvals (RLS) — HITL gate for risky tool calls

Revision ID: 0006_phase11_tools
Revises: 0005_phase10_telemetry
Create Date: 2026-09-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_phase11_tools"
down_revision = "0005_phase10_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_tool_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("tool_args", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pending_tool_approvals_tenant_id", "pending_tool_approvals", ["tenant_id"])
    op.create_index("ix_pending_tool_approvals_user_id", "pending_tool_approvals", ["user_id"])
    op.create_index("ix_pending_tool_approvals_conversation_id", "pending_tool_approvals", ["conversation_id"])
    op.create_index("ix_pending_tool_approvals_status", "pending_tool_approvals", ["status"])

    op.execute("ALTER TABLE pending_tool_approvals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pending_tool_approvals FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON pending_tool_approvals
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON pending_tool_approvals")
    op.drop_table("pending_tool_approvals")
