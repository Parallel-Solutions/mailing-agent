"""Add non-destructive PDF overlay editor state.

Revision ID: 0008_pdf_overlay_editor
Revises: 0007_template_pdf_artifacts
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_pdf_overlay_editor"
down_revision = "0007_template_pdf_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_versions", sa.Column("editor_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("template_versions", "editor_state")