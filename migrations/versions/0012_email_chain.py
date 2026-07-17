"""Email chain branch tokens for click tracking.

Revision ID: 0012_email_chain
Revises: 0011_template_version_cols
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_email_chain"
down_revision = "0011_template_version_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_chain_tokens",
        sa.Column("token", sa.String(length=36), primary_key=True),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("edge_id", sa.String(length=64), nullable=False),
        sa.Column("source_node_id", sa.String(length=64), nullable=False),
        sa.Column("target_node_id", sa.String(length=64), nullable=False),
        sa.Column("send_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("idx_chain_tokens_campaign", "campaign_chain_tokens", ["campaign_id"])
    op.create_index("idx_chain_tokens_recipient", "campaign_chain_tokens", ["campaign_id", "recipient_id"])
    op.create_index("idx_chain_tokens_edge", "campaign_chain_tokens", ["campaign_id", "edge_id"])


def downgrade() -> None:
    op.drop_index("idx_chain_tokens_edge", table_name="campaign_chain_tokens")
    op.drop_index("idx_chain_tokens_recipient", table_name="campaign_chain_tokens")
    op.drop_index("idx_chain_tokens_campaign", table_name="campaign_chain_tokens")
    op.drop_table("campaign_chain_tokens")
