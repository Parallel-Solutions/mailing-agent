"""Default campaign schedule on_error to skip.

Revision ID: 0017_on_error_skip
Revises: 0016_companies
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_on_error_skip"
down_revision = "0016_companies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE campaign_schedules SET on_error = 'skip' WHERE on_error = 'retry'")
    op.alter_column("campaign_schedules", "on_error", server_default=sa.text("'skip'"))


def downgrade() -> None:
    op.alter_column("campaign_schedules", "on_error", server_default=sa.text("'retry'"))
