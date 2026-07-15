"""Add user SMTP mailboxes.

Revision ID: 0004_smtp_mailboxes
Revises: 0003_send_queue_suppression
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_smtp_mailboxes"
down_revision = "0003_send_queue_suppression"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "smtp_mailboxes" in set(inspector.get_table_names()):
        return
    op.create_table(
        "smtp_mailboxes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="custom"),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("use_starttls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_smtp_mailboxes_owner", "smtp_mailboxes", ["owner_username"], unique=False)
    op.create_index("idx_smtp_mailboxes_owner_default", "smtp_mailboxes", ["owner_username", "is_default"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_smtp_mailboxes_owner_default", table_name="smtp_mailboxes")
    op.drop_index("idx_smtp_mailboxes_owner", table_name="smtp_mailboxes")
    op.drop_table("smtp_mailboxes")
