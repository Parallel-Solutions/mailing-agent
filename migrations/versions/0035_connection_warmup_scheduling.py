"""Add per-message scheduling and content settings to sender warmup.

Revision ID: 0035_warmup_scheduling
Revises: 0034_warmup_recipient_consent
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035_warmup_scheduling"
down_revision = "0034_warmup_recipient_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connection_warmup_programs",
        sa.Column("daily_end_time", sa.String(length=5), nullable=False, server_default="18:00"),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("pause_campaigns_during_warmup", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("subject_templates", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("body_templates", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("connection_warmup_programs", sa.Column("pause_reason", sa.Text(), nullable=True))
    op.add_column("connection_warmup_deliveries", sa.Column("task_id", sa.String(length=36), nullable=True))
    op.create_index("idx_connection_warmup_delivery_task", "connection_warmup_deliveries", ["task_id"])
    op.create_index("idx_connection_warmup_delivery_provider_message", "connection_warmup_deliveries", ["provider_message_id"])


def downgrade() -> None:
    op.drop_index("idx_connection_warmup_delivery_provider_message", table_name="connection_warmup_deliveries")
    op.drop_index("idx_connection_warmup_delivery_task", table_name="connection_warmup_deliveries")
    op.drop_column("connection_warmup_deliveries", "task_id")
    op.drop_column("connection_warmup_programs", "pause_reason")
    op.drop_column("connection_warmup_programs", "body_templates")
    op.drop_column("connection_warmup_programs", "subject_templates")
    op.drop_column("connection_warmup_programs", "pause_campaigns_during_warmup")
    op.drop_column("connection_warmup_programs", "daily_end_time")