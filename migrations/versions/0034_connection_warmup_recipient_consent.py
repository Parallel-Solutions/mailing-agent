"""Add recipient consent confirmation to sender warmup.

Revision ID: 0034_warmup_recipient_consent
Revises: 0033_connection_sender_warmup
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0034_warmup_recipient_consent"
down_revision = "0033_connection_sender_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connection_warmup_programs",
        sa.Column(
            "recipients_consent_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "connection_warmup_programs",
        sa.Column("recipients_consent_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connection_warmup_programs", "recipients_consent_confirmed_at")
    op.drop_column("connection_warmup_programs", "recipients_consent_confirmed")