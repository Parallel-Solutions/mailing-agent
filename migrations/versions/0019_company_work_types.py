"""Add company work types catalogue.

Revision ID: 0019_company_work_types
Revises: 0018_strip_email_footers
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0019_company_work_types"
down_revision = "0018_strip_email_footers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("work_types", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("companies", "work_types")
