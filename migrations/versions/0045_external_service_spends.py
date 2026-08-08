"""Ledger of billed calls to external services (LLM, email providers, validation, lookups).

Revision ID: 0045_external_service_spends
Revises: 0044_delivery_guard_cycles
Create Date: 2026-08-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0045_external_service_spends"
down_revision = "0044_delivery_guard_cycles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_service_spends",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("request_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("owner_username", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column(
            "request_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_external_service_spends_created",
        "external_service_spends",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "idx_external_service_spends_service_created",
        "external_service_spends",
        ["service", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_external_service_spends_job",
        "external_service_spends",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_external_service_spends_job", table_name="external_service_spends")
    op.drop_index("idx_external_service_spends_service_created", table_name="external_service_spends")
    op.drop_index("idx_external_service_spends_created", table_name="external_service_spends")
    op.drop_table("external_service_spends")
