"""Campaign status transition audit log.

Revision ID: 0025_campaign_status_events
Revises: 0024_delivery_channel_guard
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0025_campaign_status_events"
down_revision = "0024_delivery_channel_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_status_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_campaign_status_events_campaign_created",
        "campaign_status_events",
        ["campaign_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_campaign_status_events_campaign_created",
        table_name="campaign_status_events",
    )
    op.drop_table("campaign_status_events")
