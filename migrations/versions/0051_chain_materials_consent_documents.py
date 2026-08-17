"""Store consent documents for materials-request chain clicks.

Revision ID: 0051_chain_consent_docs
Revises: 0050_drop_send_guard_state
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "0051_chain_consent_docs"
down_revision = "0050_drop_send_guard_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("confirmed_ip", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("confirmed_user_agent", sa.Text(), nullable=True),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column(
            "evidence_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("consent_document_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("consent_document_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("document_status", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("document_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "campaign_chain_consent_events",
        sa.Column("document_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_chain_consent_events", "document_generated_at")
    op.drop_column("campaign_chain_consent_events", "document_error")
    op.drop_column("campaign_chain_consent_events", "document_status")
    op.drop_column("campaign_chain_consent_events", "consent_document_sha256")
    op.drop_column("campaign_chain_consent_events", "consent_document_path")
    op.drop_column("campaign_chain_consent_events", "evidence_payload")
    op.drop_column("campaign_chain_consent_events", "confirmed_user_agent")
    op.drop_column("campaign_chain_consent_events", "confirmed_ip")
    op.drop_column("campaign_chain_consent_events", "confirmed_at")
