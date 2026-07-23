"""Add delivery_email to delivery_attempts.

Revision ID: 0022_delivery_attempt_email
Revises: 0021_chain_token_test_email
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_delivery_attempt_email"
down_revision = "0021_chain_token_test_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_attempts",
        sa.Column("delivery_email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("delivery_attempts", "delivery_email")
