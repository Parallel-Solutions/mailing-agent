"""Font assets and template font requirements.

Revision ID: 0028_font_assets
Revises: 0027_template_attachment_format
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0028_font_assets"
down_revision = "0027_template_attachment_format"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "font_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("family", sa.String(length=255), nullable=False),
        sa.Column("family_normalized", sa.String(length=255), nullable=False),
        sa.Column("subfamily", sa.String(length=128), nullable=False, server_default="Regular"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="400"),
        sa.Column("italic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("postscript_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="upload"),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("license_type", sa.String(length=64), nullable=False, server_default="user_confirmed"),
        sa.Column("license_url", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("license_storage_key", sa.String(length=512), nullable=True),
        sa.Column("embedding_permissions", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("glyph_coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_font_assets_owner_family",
        "font_assets",
        ["owner_username", "family_normalized", "status"],
    )
    op.create_index(
        "uq_font_assets_owner_sha256",
        "font_assets",
        ["owner_username", "sha256"],
        unique=True,
    )

    op.create_table(
        "template_font_requirements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_version_id", sa.String(length=36), nullable=False),
        sa.Column("family", sa.String(length=255), nullable=False),
        sa.Column("family_normalized", sa.String(length=255), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="400"),
        sa.Column("italic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_font_asset_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="document"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="missing"),
        sa.Column("fallback_family", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["resolved_font_asset_id"],
            ["font_assets.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_template_font_requirements_version",
        "template_font_requirements",
        ["template_version_id"],
    )
    op.create_index(
        "uq_template_font_requirement_signature",
        "template_font_requirements",
        ["template_version_id", "family_normalized", "weight", "italic"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_template_font_requirement_signature", table_name="template_font_requirements")
    op.drop_index("idx_template_font_requirements_version", table_name="template_font_requirements")
    op.drop_table("template_font_requirements")
    op.drop_index("uq_font_assets_owner_sha256", table_name="font_assets")
    op.drop_index("idx_font_assets_owner_family", table_name="font_assets")
    op.drop_table("font_assets")
