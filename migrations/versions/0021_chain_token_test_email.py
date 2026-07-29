"""Add test_email to campaign_chain_tokens for interactive test chains.

Revision ID: 0021_chain_token_test_email
Revises: 0020_company_document_numbers
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_chain_token_test_email"
down_revision = "0020_company_document_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_chain_tokens",
        sa.Column("test_email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_chain_tokens", "test_email")
