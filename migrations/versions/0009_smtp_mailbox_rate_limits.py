"""Add per-connection hourly/daily send rate limits.

Revision ID: 0009_smtp_mailbox_rate_limits
Revises: 0008_pdf_overlay_editor
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_smtp_mailbox_rate_limits"
down_revision = "0008_pdf_overlay_editor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("smtp_mailboxes")}
    if "max_per_hour" not in columns:
        op.add_column(
            "smtp_mailboxes",
            sa.Column("max_per_hour", sa.Integer(), nullable=False, server_default="0"),
        )
    if "max_per_day" not in columns:
        op.add_column(
            "smtp_mailboxes",
            sa.Column("max_per_day", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("smtp_mailboxes", "max_per_day")
    op.drop_column("smtp_mailboxes", "max_per_hour")
