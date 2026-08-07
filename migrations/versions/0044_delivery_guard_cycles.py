"""Track delivery errors cumulatively between guard resets.

Revision ID: 0044_delivery_guard_cycles
Revises: 0043_email_validation_preflight
Create Date: 2026-08-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0044_delivery_guard_cycles"
down_revision = "0043_email_validation_preflight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_mailboxes",
        sa.Column(
            "delivery_guard_monitoring_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "delivery_key_guards",
        sa.Column(
            "delivery_guard_monitoring_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Provider acceptance is not a final delivery result. A later webhook will
    # promote these rows to either success or error.
    op.execute(
        """
        UPDATE delivery_channel_outcomes
        SET outcome = 'pending'
        WHERE lower(provider_status) = 'accepted'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE delivery_channel_outcomes
        SET outcome = 'success'
        WHERE lower(provider_status) = 'accepted'
        """
    )
    op.drop_column("delivery_key_guards", "delivery_guard_monitoring_started_at")
    op.drop_column("smtp_mailboxes", "delivery_guard_monitoring_started_at")
