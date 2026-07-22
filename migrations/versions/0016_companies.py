"""Add companies and company memberships.

Revision ID: 0016_companies
Revises: 0016_campaign_connection_ids
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_companies"
down_revision = "0016_campaign_connection_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("contact_person_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("logo_storage_key", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "company_memberships",
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["username"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", "username"),
        sa.UniqueConstraint("username", name="uq_company_memberships_username"),
    )
    op.create_index("idx_company_memberships_company", "company_memberships", ["company_id"])
    op.create_index("idx_company_memberships_username", "company_memberships", ["username"])


def downgrade() -> None:
    op.drop_index("idx_company_memberships_username", table_name="company_memberships")
    op.drop_index("idx_company_memberships_company", table_name="company_memberships")
    op.drop_table("company_memberships")
    op.drop_table("companies")
