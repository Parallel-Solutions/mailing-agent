"""Standalone email chains table.

Revision ID: 0014_standalone_email_chains
Revises: 0013_chain_consent_events
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_standalone_email_chains"
down_revision = "0013_chain_consent_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_chains",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_email_chains_owner", "email_chains", ["owner_username"])
    op.add_column(
        "campaigns",
        sa.Column("email_chain_id", sa.String(length=36), sa.ForeignKey("email_chains.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("idx_campaigns_email_chain", "campaigns", ["email_chain_id"])


def downgrade() -> None:
    op.drop_index("idx_campaigns_email_chain", table_name="campaigns")
    op.drop_column("campaigns", "email_chain_id")
    op.drop_index("idx_email_chains_owner", table_name="email_chains")
    op.drop_table("email_chains")
