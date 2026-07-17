"""Backfill template_versions columns for DBs stamped on legacy heads.

Revision ID: 0011_template_version_cols
Revises: 0010_document_template_type
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_template_version_cols"
down_revision = "0010_document_template_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("template_versions")}
    if "rendered_pdf_storage_key" not in columns:
        op.add_column("template_versions", sa.Column("rendered_pdf_storage_key", sa.String(length=512), nullable=True))
    if "rendered_pdf_filename" not in columns:
        op.add_column("template_versions", sa.Column("rendered_pdf_filename", sa.String(length=255), nullable=True))
    if "editor_state" not in columns:
        op.add_column("template_versions", sa.Column("editor_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("template_versions")}
    if "editor_state" in columns:
        op.drop_column("template_versions", "editor_state")
    if "rendered_pdf_filename" in columns:
        op.drop_column("template_versions", "rendered_pdf_filename")
    if "rendered_pdf_storage_key" in columns:
        op.drop_column("template_versions", "rendered_pdf_storage_key")
