"""Store the send-time email validation snapshot on delivery attempts.

Revision ID: 0048_delivery_attempt_email_validation
Revises: 0047_rusender_fixed_warmup
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "0048_delivery_attempt_email_validation"
down_revision = "0047_rusender_fixed_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_attempts",
        sa.Column(
            "email_validation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("delivery_attempts", "email_validation")
