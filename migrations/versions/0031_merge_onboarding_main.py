"""Merge onboarding and main migration heads.

Revision ID: 0031_merge_onboarding_main
Revises: 0017_merge_heads, 0030_template_source_text_cache
Create Date: 2026-07-29
"""

from __future__ import annotations


revision = "0031_merge_onboarding_main"
down_revision = (
    "0017_merge_heads",
    "0030_template_source_text_cache",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
