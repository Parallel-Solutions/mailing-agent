"""Add first-party SMTP click tracking.

Revision ID: 0041_smtp_click_tracking
Revises: 0040_provider_delivery_events
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0041_smtp_click_tracking"
down_revision = "0040_provider_delivery_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smtp_click_tracking",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("delivery_key_hash", sa.String(length=64), nullable=False),
        sa.Column("open_tracking_id", sa.String(length=36), nullable=True),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("owner_username", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("row_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("warmup_delivery_id", sa.String(length=36), nullable=True),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("send_mode", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("link_kind", sa.String(length=16), nullable=False, server_default="custom"),
        sa.Column("url_hash", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="prepared"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["open_tracking_id"], ["smtp_open_tracking.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["warmup_delivery_id"],
            ["connection_warmup_deliveries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_smtp_click_tracking_token", "smtp_click_tracking", ["token"], unique=True)
    op.create_index(
        "uq_smtp_click_tracking_delivery_url",
        "smtp_click_tracking",
        ["delivery_key_hash", "url_hash"],
        unique=True,
    )
    op.create_index(
        "idx_smtp_click_tracking_job",
        "smtp_click_tracking",
        ["job_id", "first_clicked_at"],
    )
    op.create_index(
        "idx_smtp_click_tracking_warmup",
        "smtp_click_tracking",
        ["warmup_delivery_id", "first_clicked_at"],
    )
    op.create_index(
        "idx_smtp_click_tracking_provider_message",
        "smtp_click_tracking",
        ["provider_message_id"],
    )
    op.create_index(
        "idx_smtp_click_tracking_campaign_url",
        "smtp_click_tracking",
        ["campaign_id", "url_hash"],
    )


def downgrade() -> None:
    op.drop_index("idx_smtp_click_tracking_campaign_url", table_name="smtp_click_tracking")
    op.drop_index("idx_smtp_click_tracking_provider_message", table_name="smtp_click_tracking")
    op.drop_index("idx_smtp_click_tracking_warmup", table_name="smtp_click_tracking")
    op.drop_index("idx_smtp_click_tracking_job", table_name="smtp_click_tracking")
    op.drop_index("uq_smtp_click_tracking_delivery_url", table_name="smtp_click_tracking")
    op.drop_index("uq_smtp_click_tracking_token", table_name="smtp_click_tracking")
    op.drop_table("smtp_click_tracking")
