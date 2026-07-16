"""Store the editable template source and delivery PDF together.

Revision ID: 0007_template_pdf_artifacts
Revises: 0006_campaign_flow_domain
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_template_pdf_artifacts"
down_revision = "0006_campaign_flow_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_versions", sa.Column("rendered_pdf_storage_key", sa.String(length=512), nullable=True))
    op.add_column("template_versions", sa.Column("rendered_pdf_filename", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("template_versions", "rendered_pdf_filename")
    op.drop_column("template_versions", "rendered_pdf_storage_key")
