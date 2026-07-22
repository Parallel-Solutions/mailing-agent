"""Add company document number counters and allocations.

Revision ID: 0020_company_document_numbers
Revises: 0019_company_work_types
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_company_document_numbers"
down_revision = "0019_company_work_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_document_counters",
        sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type_key", sa.String(length=128), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("company_id", "document_type_key"),
    )
    op.create_table(
        "company_document_number_allocations",
        sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type_key", sa.String(length=128), nullable=False),
        sa.Column("allocation_key", sa.String(length=255), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("company_id", "document_type_key", "allocation_key"),
    )
    op.create_index(
        "idx_company_document_allocations_key",
        "company_document_number_allocations",
        ["allocation_key"],
    )


def downgrade() -> None:
    op.drop_index("idx_company_document_allocations_key", table_name="company_document_number_allocations")
    op.drop_table("company_document_number_allocations")
    op.drop_table("company_document_counters")
