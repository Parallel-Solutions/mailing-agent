"""Document attachment output format.

Revision ID: 0027_template_attachment_format
Revises: 0026_connection_error_warmup
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0027_template_attachment_format"
down_revision = "0026_connection_error_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_templates",
        sa.Column(
            "attachment_output_format",
            sa.String(length=16),
            nullable=False,
            server_default="original",
        ),
    )


def downgrade() -> None:
    op.drop_column("mail_templates", "attachment_output_format")
