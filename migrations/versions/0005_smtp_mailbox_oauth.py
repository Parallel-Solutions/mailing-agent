"""Extend SMTP mailboxes with OAuth fields.

Revision ID: 0005_smtp_mailbox_oauth
Revises: 0004_smtp_mailboxes
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_smtp_mailbox_oauth"
down_revision = "0004_smtp_mailboxes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("smtp_mailboxes")}
    if "auth_method" not in columns:
        op.add_column(
            "smtp_mailboxes",
            sa.Column("auth_method", sa.String(length=16), nullable=False, server_default="password"),
        )
    if "oauth_provider" not in columns:
        op.add_column("smtp_mailboxes", sa.Column("oauth_provider", sa.String(length=32), nullable=True))
    if "oauth_tokens_encrypted" not in columns:
        op.add_column("smtp_mailboxes", sa.Column("oauth_tokens_encrypted", sa.Text(), nullable=True))
    if "smtp_username" not in columns:
        op.add_column("smtp_mailboxes", sa.Column("smtp_username", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("smtp_mailboxes", "smtp_username")
    op.drop_column("smtp_mailboxes", "oauth_tokens_encrypted")
    op.drop_column("smtp_mailboxes", "oauth_provider")
    op.drop_column("smtp_mailboxes", "auth_method")
