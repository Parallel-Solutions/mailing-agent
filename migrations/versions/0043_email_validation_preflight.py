"""Add persistent email validation cache and validation runs.

Revision ID: 0043_email_validation_preflight
Revises: 0042_delivery_key_guards
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0043_email_validation_preflight"
down_revision = "0042_delivery_key_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_validation_cache",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="smtpbz"),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("reason_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_email_validation_cache_owner_provider_email",
        "email_validation_cache",
        ["owner_username", "provider", "normalized_email"],
        unique=True,
    )
    op.create_index(
        "idx_email_validation_cache_expires",
        "email_validation_cache",
        ["provider", "expires_at"],
        unique=False,
    )

    op.create_table(
        "email_validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="smtpbz"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_email_validation_runs_scope",
        "email_validation_runs",
        ["owner_username", "scope_type", "scope_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_email_validation_runs_status",
        "email_validation_runs",
        ["status", "created_at"],
        unique=False,
    )

    # Legacy "valid" meant syntax-only validation. Mark unsent rows as pending
    # so they cannot be presented or launched as SMTP.BZ-verified.
    op.execute(
        "UPDATE audience_members SET validation_status = 'pending' "
        "WHERE validation_status = 'valid'"
    )
    op.execute(
        "UPDATE campaign_recipients SET validation_status = 'pending' "
        "WHERE validation_status = 'valid' AND send_status = 'pending'"
    )


def downgrade() -> None:
    op.drop_index("idx_email_validation_runs_status", table_name="email_validation_runs")
    op.drop_index("idx_email_validation_runs_scope", table_name="email_validation_runs")
    op.drop_table("email_validation_runs")
    op.drop_index("idx_email_validation_cache_expires", table_name="email_validation_cache")
    op.drop_index("uq_email_validation_cache_owner_provider_email", table_name="email_validation_cache")
    op.drop_table("email_validation_cache")
