"""Add IMAP INBOX bounce/complaint scanning (DSN/NDR + ARF/FBL).

Revision ID: 0042_imap_inbox_bounce_scan
Revises: 0041_smtp_click_tracking
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0042_imap_inbox_bounce_scan"
down_revision = "0041_smtp_click_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_mailboxes",
        sa.Column("bounce_scan_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("bounce_scan_last_uid", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("bounce_scan_uidvalidity", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("bounce_scan_last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("bounce_scan_last_error", sa.Text(), nullable=True),
    )

    op.create_table(
        "smtp_inbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("report_format", sa.String(length=16), nullable=False),
        sa.Column("imap_uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id_hash", sa.String(length=64), nullable=False),
        sa.Column("final_recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=16), nullable=True),
        sa.Column("status_code", sa.String(length=16), nullable=True),
        sa.Column("diagnostic_code", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("matched_job_id", sa.String(length=64), nullable=True),
        sa.Column("matched_campaign_id", sa.String(length=36), nullable=True),
        sa.Column("matched_by", sa.String(length=16), nullable=True),
        sa.Column("suppression_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_smtp_inbox_events_dedup",
        "smtp_inbox_events",
        ["connection_id", "message_id_hash"],
        unique=True,
    )
    op.create_index(
        "idx_smtp_inbox_events_recipient",
        "smtp_inbox_events",
        ["final_recipient", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_smtp_inbox_events_recipient", table_name="smtp_inbox_events")
    op.drop_index("uq_smtp_inbox_events_dedup", table_name="smtp_inbox_events")
    op.drop_table("smtp_inbox_events")

    op.drop_column("smtp_mailboxes", "bounce_scan_last_error")
    op.drop_column("smtp_mailboxes", "bounce_scan_last_checked_at")
    op.drop_column("smtp_mailboxes", "bounce_scan_uidvalidity")
    op.drop_column("smtp_mailboxes", "bounce_scan_last_uid")
    op.drop_column("smtp_mailboxes", "bounce_scan_enabled")
