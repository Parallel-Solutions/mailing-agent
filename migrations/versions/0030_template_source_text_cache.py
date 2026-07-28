"""Cache extracted template source text.

Revision ID: 0030_template_source_text_cache
Revises: 0029_fallback_email_text
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0030_template_source_text_cache"
down_revision = "0029_fallback_email_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "template_versions", sa.Column("source_text", sa.Text(), nullable=True)
    )
    op.add_column(
        "template_versions",
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column(
            "text_extraction_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "template_versions",
        sa.Column("text_extraction_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column("text_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE template_versions "
            "SET source_text = CONCAT_WS(E'\\n', subject, body_html, body_text), "
            "text_extraction_status = 'ready', "
            "text_extracted_at = NOW() "
            "WHERE storage_key IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("template_versions", "text_extracted_at")
    op.drop_column("template_versions", "text_extraction_error")
    op.drop_column("template_versions", "text_extraction_status")
    op.drop_column("template_versions", "source_sha256")
    op.drop_column("template_versions", "source_text")
