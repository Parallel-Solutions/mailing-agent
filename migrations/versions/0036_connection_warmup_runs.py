"""Preserve sender warmup history across repeated runs.

Revision ID: 0036_connection_warmup_runs
Revises: 0035_warmup_scheduling
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0036_connection_warmup_runs"
down_revision = "0035_warmup_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connection_warmup_programs",
        sa.Column("run_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "connection_warmup_deliveries",
        sa.Column("run_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_index(
        "uq_connection_warmup_delivery_recipient_day",
        table_name="connection_warmup_deliveries",
    )
    op.create_index(
        "uq_connection_warmup_delivery_recipient_day",
        "connection_warmup_deliveries",
        ["program_id", "recipient_id", "run_number", "day_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_connection_warmup_delivery_recipient_day",
        table_name="connection_warmup_deliveries",
    )
    op.create_index(
        "uq_connection_warmup_delivery_recipient_day",
        "connection_warmup_deliveries",
        ["program_id", "recipient_id", "day_number"],
        unique=True,
    )
    op.drop_column("connection_warmup_deliveries", "run_number")
    op.drop_column("connection_warmup_programs", "run_number")
