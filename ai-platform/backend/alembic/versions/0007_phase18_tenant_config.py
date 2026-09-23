"""Phase 18: tenants.config_overrides — the tenant layer of the hierarchical
config resolver (§7), wired to real storage for the first time. The resolver
itself has always supported a tenant layer; every call site just always
passed platform-defaults alone, so the "hierarchy" was decorative until now.

Revision ID: 0007_phase18_tenant_config
Revises: 0006_phase11_tools
Create Date: 2026-09-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_phase18_tenant_config"
down_revision = "0006_phase11_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("config_overrides", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "config_overrides")
