"""Add RuSender sending key ID to delivery connections.

Revision ID: 0032_rusender_sending_key_id
Revises: 0031_merge_onboarding_main
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0032_rusender_sending_key_id"
down_revision = "0031_merge_onboarding_main"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("smtp_mailboxes")}
    if "sending_key_id" not in columns:
        op.add_column(
            "smtp_mailboxes",
            sa.Column("sending_key_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("smtp_mailboxes", "sending_key_id")
