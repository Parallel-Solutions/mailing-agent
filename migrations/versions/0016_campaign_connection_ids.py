"""Add connection_ids to campaigns for multi-sender support.

Revision ID: 0016_campaign_connection_ids
Revises: 0015_mail_template_is_template
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0016_campaign_connection_ids"
down_revision = "0015_mail_template_is_template"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("connection_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.execute(
        """
        UPDATE campaigns
        SET connection_ids = jsonb_build_array(smtp_mailbox_id)
        WHERE smtp_mailbox_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("campaigns", "connection_ids")
