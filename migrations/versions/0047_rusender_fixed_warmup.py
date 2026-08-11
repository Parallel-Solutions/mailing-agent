"""Add fixed per-recipient RuSender warmup settings.

Revision ID: 0047_rusender_fixed_warmup
Revises: 0046_merge_smtpbz_spends
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "0047_rusender_fixed_warmup"
down_revision = "0046_merge_smtpbz_spends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connection_warmup_programs",
        sa.Column("warmup_mode", sa.String(length=24), nullable=False, server_default="growth"),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="14"),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("suspended_by_campaign", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connection_warmup_recipients",
        sa.Column("messages_per_day", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "connection_warmup_deliveries",
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connection_warmup_deliveries",
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("connection_warmup_deliveries", "open_count")
    op.drop_column("connection_warmup_deliveries", "opened_at")
    op.drop_column("connection_warmup_recipients", "messages_per_day")
    op.drop_column("connection_warmup_programs", "suspended_at")
    op.drop_column("connection_warmup_programs", "suspended_by_campaign")
    op.drop_column("connection_warmup_programs", "duration_days")
    op.drop_column("connection_warmup_programs", "warmup_mode")
