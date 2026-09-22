"""Phase 7: user memory consent flag + user_memories table (RLS)

Revision ID: 0004_phase7_memory
Revises: 0003_phase5_rag
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_phase7_memory"
down_revision = "0003_phase5_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("memory_consent", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "user_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_user_memories_user_id_key"),
    )
    op.create_index("ix_user_memories_tenant_id", "user_memories", ["tenant_id"])
    op.create_index("ix_user_memories_user_id", "user_memories", ["user_id"])

    op.execute("ALTER TABLE user_memories ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_memories FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON user_memories
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON user_memories")
    op.drop_table("user_memories")
    op.drop_column("users", "memory_consent")
