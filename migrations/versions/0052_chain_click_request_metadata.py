"""Store request metadata for the first tracked chain click.

Revision ID: 0052_chain_click_metadata
Revises: 0051_chain_consent_docs
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "0052_chain_click_metadata"
down_revision = "0051_chain_consent_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_chain_tokens",
        sa.Column("clicked_ip", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "campaign_chain_tokens",
        sa.Column("clicked_user_agent", sa.Text(), nullable=True),
    )
    op.add_column(
        "campaign_chain_tokens",
        sa.Column("clicked_http_method", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_chain_tokens", "clicked_http_method")
    op.drop_column("campaign_chain_tokens", "clicked_user_agent")
    op.drop_column("campaign_chain_tokens", "clicked_ip")
