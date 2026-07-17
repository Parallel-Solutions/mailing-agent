"""Chain subscribe/unsubscribe consent events.

Revision ID: 0013_chain_consent_events
Revises: 0012_email_chain
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_chain_consent_events"
down_revision = "0012_email_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_chain_consent_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("edge_id", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_chain_consent_campaign_action",
        "campaign_chain_consent_events",
        ["campaign_id", "action"],
    )
    op.create_index(
        "idx_chain_consent_email_action_expires",
        "campaign_chain_consent_events",
        ["email", "action", "expires_at"],
    )
    op.create_index(
        "idx_chain_consent_token",
        "campaign_chain_consent_events",
        ["token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_chain_consent_token", table_name="campaign_chain_consent_events")
    op.drop_index("idx_chain_consent_email_action_expires", table_name="campaign_chain_consent_events")
    op.drop_index("idx_chain_consent_campaign_action", table_name="campaign_chain_consent_events")
    op.drop_table("campaign_chain_consent_events")
