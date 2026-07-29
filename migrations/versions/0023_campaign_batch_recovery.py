"""Campaign batch worker recovery limits and on_error pause migration.

Revision ID: 0023_campaign_batch_recovery
Revises: 0022_delivery_attempt_email
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_campaign_batch_recovery"
down_revision = "0022_delivery_attempt_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_batches",
        sa.Column("worker_recovery_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE campaign_schedules SET on_error = 'skip' WHERE on_error = 'pause'")


def downgrade() -> None:
    op.drop_column("campaign_batches", "worker_recovery_count")
