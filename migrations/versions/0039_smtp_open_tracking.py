"""Add first-party SMTP open tracking.

Revision ID: 0039_smtp_open_tracking
Revises: 0038_smtp_imap_sent_copy
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0039_smtp_open_tracking"
down_revision = "0038_smtp_imap_sent_copy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smtp_open_tracking",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("delivery_key_hash", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("owner_username", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("row_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("warmup_delivery_id", sa.String(length=36), nullable=True),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("send_mode", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="prepared"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["warmup_delivery_id"],
            ["connection_warmup_deliveries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_smtp_open_tracking_token", "smtp_open_tracking", ["token"], unique=True)
    op.create_index(
        "uq_smtp_open_tracking_delivery_key",
        "smtp_open_tracking",
        ["delivery_key_hash"],
        unique=True,
    )
    op.create_index(
        "idx_smtp_open_tracking_job",
        "smtp_open_tracking",
        ["job_id", "first_opened_at"],
    )
    op.create_index(
        "idx_smtp_open_tracking_warmup",
        "smtp_open_tracking",
        ["warmup_delivery_id", "first_opened_at"],
    )
    op.create_index(
        "idx_smtp_open_tracking_provider_message",
        "smtp_open_tracking",
        ["provider_message_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_smtp_open_tracking_provider_message", table_name="smtp_open_tracking")
    op.drop_index("idx_smtp_open_tracking_warmup", table_name="smtp_open_tracking")
    op.drop_index("idx_smtp_open_tracking_job", table_name="smtp_open_tracking")
    op.drop_index("uq_smtp_open_tracking_delivery_key", table_name="smtp_open_tracking")
    op.drop_index("uq_smtp_open_tracking_token", table_name="smtp_open_tracking")
    op.drop_table("smtp_open_tracking")
