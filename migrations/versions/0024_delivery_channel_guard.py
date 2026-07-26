"""Per-connection delivery error guard and global send slots.

Revision ID: 0024_delivery_channel_guard
Revises: 0023_campaign_batch_recovery
Create Date: 2026-07-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0024_delivery_channel_guard"
down_revision = "0023_campaign_batch_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_error_rate_threshold", sa.Float(), nullable=False, server_default="0.05"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_error_window_minutes", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_error_min_samples", sa.Integer(), nullable=False, server_default="20"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_error_critical_count", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_error_action", sa.String(length=16), nullable=False, server_default="throttle"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_throttled_max_per_hour", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_state", sa.String(length=16), nullable=False, server_default="normal"),
    )
    op.add_column("smtp_mailboxes", sa.Column("delivery_guard_reason", sa.Text(), nullable=True))
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_terminal_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_error_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_error_rate", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_triggered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("delivery_guard_last_error_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "delivery_channel_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("provider_status", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("smtp_response", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_delivery_channel_outcomes_message",
        "delivery_channel_outcomes",
        ["connection_id", "provider_message_id"],
        unique=True,
    )
    op.create_index(
        "idx_delivery_channel_outcomes_window",
        "delivery_channel_outcomes",
        ["connection_id", "occurred_at"],
    )

    op.create_table(
        "delivery_channel_send_slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_delivery_channel_send_slots_window",
        "delivery_channel_send_slots",
        ["connection_id", "reserved_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_delivery_channel_send_slots_window", table_name="delivery_channel_send_slots")
    op.drop_table("delivery_channel_send_slots")
    op.drop_index("idx_delivery_channel_outcomes_window", table_name="delivery_channel_outcomes")
    op.drop_index("uq_delivery_channel_outcomes_message", table_name="delivery_channel_outcomes")
    op.drop_table("delivery_channel_outcomes")
    for column in (
        "delivery_guard_last_error_at",
        "delivery_guard_triggered_at",
        "delivery_guard_error_rate",
        "delivery_guard_error_count",
        "delivery_guard_terminal_count",
        "delivery_guard_reason",
        "delivery_guard_state",
        "delivery_throttled_max_per_hour",
        "delivery_error_action",
        "delivery_error_critical_count",
        "delivery_error_min_samples",
        "delivery_error_window_minutes",
        "delivery_error_rate_threshold",
        "delivery_guard_enabled",
    ):
        op.drop_column("smtp_mailboxes", column)
