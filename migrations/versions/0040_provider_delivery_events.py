"""Add typed provider_delivery_events + provider_task_lookup tables.

Revision ID: 0040_provider_delivery_events
Revises: 0039_smtp_open_tracking
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0040_provider_delivery_events"
down_revision = "0039_smtp_open_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_delivery_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("provider_task_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("row_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("provider_status", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("smtp_response", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_provider_delivery_events_key",
        "provider_delivery_events",
        ["source", "event_key"],
        unique=True,
    )
    op.create_index(
        "idx_provider_delivery_events_job_recipient",
        "provider_delivery_events",
        ["job_id", "recipient"],
    )
    op.create_index(
        "idx_provider_delivery_events_campaign_time",
        "provider_delivery_events",
        ["campaign_id", "occurred_at"],
    )
    op.create_index(
        "idx_provider_delivery_events_task",
        "provider_delivery_events",
        ["source", "provider_task_id"],
    )
    op.create_index(
        "idx_provider_delivery_events_job_status",
        "provider_delivery_events",
        ["job_id", "provider_status"],
    )

    op.create_table(
        "provider_task_lookup",
        sa.Column("provider_task_id", sa.String(length=255), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("row_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("provider_task_id"),
    )
    op.create_index("idx_provider_task_lookup_job", "provider_task_lookup", ["job_id"])


def downgrade() -> None:
    op.drop_index("idx_provider_task_lookup_job", table_name="provider_task_lookup")
    op.drop_table("provider_task_lookup")

    op.drop_index("idx_provider_delivery_events_job_status", table_name="provider_delivery_events")
    op.drop_index("idx_provider_delivery_events_task", table_name="provider_delivery_events")
    op.drop_index("idx_provider_delivery_events_campaign_time", table_name="provider_delivery_events")
    op.drop_index("idx_provider_delivery_events_job_recipient", table_name="provider_delivery_events")
    op.drop_index("uq_provider_delivery_events_key", table_name="provider_delivery_events")
    op.drop_table("provider_delivery_events")
