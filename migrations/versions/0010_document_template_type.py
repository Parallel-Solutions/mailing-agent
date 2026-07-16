"""Unify kp/contract template types into document.

Revision ID: 0010_document_template_type
Revises: 0009_smtp_mailbox_rate_limits
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_document_template_type"
down_revision = "0009_smtp_mailbox_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE mail_templates SET template_type = 'document' "
            "WHERE template_type IN ('kp', 'contract')"
        )
    )


def downgrade() -> None:
    # Irreversible without original subtype; leave document rows as-is.
    pass
