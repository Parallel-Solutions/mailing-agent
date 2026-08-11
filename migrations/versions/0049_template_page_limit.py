"""Add a per-template one-page PDF layout switch.

Revision ID: 0049_template_page_limit
Revises: 0048_delivery_email_validation
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "0049_template_page_limit"
down_revision = "0048_delivery_email_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_templates",
        sa.Column(
            "enforce_one_page",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("mail_templates", "enforce_one_page")
