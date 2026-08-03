"""Add user-managed connection sender warmup programs.

Revision ID: 0033_connection_sender_warmup
Revises: 0032_rusender_sending_key_id
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0033_connection_sender_warmup"
down_revision = "0032_rusender_sending_key_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection_warmup_programs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("daily_start_time", sa.String(length=5), nullable=False),
        sa.Column("max_growth_percent", sa.Integer(), nullable=False),
        sa.Column("current_day", sa.Integer(), nullable=False),
        sa.Column("daily_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diagnostics_status", sa.String(length=24), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scheduled_task_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_connection_warmup_program_connection", "connection_warmup_programs", ["connection_id"], unique=True)
    op.create_index("idx_connection_warmup_program_owner", "connection_warmup_programs", ["owner_username"])

    op.create_table(
        "connection_warmup_recipients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("program_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["program_id"], ["connection_warmup_programs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_connection_warmup_recipient_email", "connection_warmup_recipients", ["program_id", "email"], unique=True)
    op.create_index("idx_connection_warmup_recipient_status", "connection_warmup_recipients", ["program_id", "status"])

    op.create_table(
        "connection_warmup_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("program_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_id", sa.String(length=36), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["program_id"], ["connection_warmup_programs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["connection_warmup_recipients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_connection_warmup_delivery_program_day", "connection_warmup_deliveries", ["program_id", "day_number"])
    op.create_index("idx_connection_warmup_delivery_recipient", "connection_warmup_deliveries", ["recipient_id", "scheduled_at"])
    op.create_index(
        "uq_connection_warmup_delivery_recipient_day",
        "connection_warmup_deliveries",
        ["program_id", "recipient_id", "day_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("connection_warmup_deliveries")
    op.drop_table("connection_warmup_recipients")
    op.drop_table("connection_warmup_programs")