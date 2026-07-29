"""Remove the length limit from recipient fallback email lists.

Revision ID: 0029_fallback_email_text
Revises: 0028_font_assets
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0029_fallback_email_text"
down_revision = "0028_font_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audience_members",
        "email_fallback",
        existing_type=sa.String(length=320),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "campaign_recipients",
        "email_fallback",
        existing_type=sa.String(length=320),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE audience_members "
            "SET email_fallback = LEFT(email_fallback, 320) "
            "WHERE LENGTH(email_fallback) > 320"
        )
    )
    op.execute(
        sa.text(
            "UPDATE campaign_recipients "
            "SET email_fallback = LEFT(email_fallback, 320) "
            "WHERE LENGTH(email_fallback) > 320"
        )
    )
    op.alter_column(
        "campaign_recipients",
        "email_fallback",
        existing_type=sa.Text(),
        type_=sa.String(length=320),
        existing_nullable=False,
    )
    op.alter_column(
        "audience_members",
        "email_fallback",
        existing_type=sa.Text(),
        type_=sa.String(length=320),
        existing_nullable=False,
    )
