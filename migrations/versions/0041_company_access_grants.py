"""Add scoped company access grants.

Revision ID: 0041_company_access_grants
Revises: 0040_repair_detached_chains
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0041_company_access_grants"
down_revision = "0040_repair_detached_chains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_access_grants",
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column(
            "access_level", sa.String(length=16), nullable=False, server_default="view"
        ),
        sa.Column("created_by", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["username"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", "username"),
    )
    op.create_index(
        "idx_company_access_grants_username", "company_access_grants", ["username"]
    )
    op.create_index(
        "idx_company_access_grants_company", "company_access_grants", ["company_id"]
    )

    # Preserve the effective permissions of existing company administrators.
    op.execute(
        """
        INSERT INTO company_access_grants (
            company_id, username, access_level, created_by, created_at
        )
        SELECT company_id, username, 'manage', 'migration', now()
        FROM company_memberships
        WHERE role = 'company_admin'
        ON CONFLICT (company_id, username) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE users AS u
        SET role = 'company_admin'
        WHERE u.role <> 'admin'
          AND EXISTS (
              SELECT 1
              FROM company_memberships AS cm
              WHERE cm.username = u.username
                AND cm.role = 'company_admin'
          )
        """
    )


def downgrade() -> None:
    op.drop_index("idx_company_access_grants_company", table_name="company_access_grants")
    op.drop_index("idx_company_access_grants_username", table_name="company_access_grants")
    op.drop_table("company_access_grants")
