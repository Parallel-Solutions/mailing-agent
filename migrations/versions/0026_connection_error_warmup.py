"""Replace delivery throttling with connection warmup.

Revision ID: 0026_connection_error_warmup
Revises: 0025_campaign_status_events
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0026_connection_error_warmup"
down_revision = "0025_campaign_status_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_mailboxes",
        sa.Column(
            "warmup_recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("warmup_percent_of_errors", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column("smtp_mailboxes", sa.Column("warmup_task_id", sa.String(length=36), nullable=True))
    op.add_column(
        "smtp_mailboxes",
        sa.Column("warmup_status", sa.String(length=16), nullable=False, server_default="idle"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("warmup_sent_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("warmup_error_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("warmup_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("warmup_completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Старые ограничения не должны продолжать незаметно снижать скорость.
    op.execute(
        sa.text(
            "UPDATE smtp_mailboxes "
            "SET delivery_guard_enabled = false, "
            "delivery_error_action = 'warmup', "
            "delivery_guard_state = 'normal', "
            "delivery_guard_reason = NULL "
            "WHERE delivery_error_action IN ('throttle', 'disable')"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE smtp_mailboxes "
            "SET delivery_guard_enabled = false, delivery_error_action = 'throttle' "
            "WHERE delivery_error_action = 'warmup'"
        )
    )
    for column in (
        "warmup_completed_at",
        "warmup_started_at",
        "warmup_error_count",
        "warmup_sent_count",
        "warmup_status",
        "warmup_task_id",
        "warmup_percent_of_errors",
        "warmup_recipients",
    ):
        op.drop_column("smtp_mailboxes", column)
