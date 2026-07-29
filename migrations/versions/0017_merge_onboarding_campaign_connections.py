"""Merge onboarding and campaign connection migration heads.

Revision ID: 0017_merge_heads
Revises: 0016_user_onboarding_state, 0016_campaign_connection_ids
Create Date: 2026-07-20
"""

from __future__ import annotations


revision = "0017_merge_heads"
down_revision = (
    "0016_user_onboarding_state",
    "0016_campaign_connection_ids",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass