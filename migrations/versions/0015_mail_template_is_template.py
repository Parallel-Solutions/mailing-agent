"""Add is_template flag to mail_templates.

Revision ID: 0015_mail_template_is_template
Revises: 0014_standalone_email_chains
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_mail_template_is_template"
down_revision = "0014_standalone_email_chains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_templates",
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("mail_templates", "is_template")
