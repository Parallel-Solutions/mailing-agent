"""Add per-connection IMAP settings and sent-copy outcomes.

Revision ID: 0038_smtp_imap_sent_copy
Revises: 0037_fix_warmup_volume
Create Date: 2026-08-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0038_smtp_imap_sent_copy"
down_revision = "0037_fix_warmup_volume"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smtp_mailboxes",
        sa.Column("save_sent_copy", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("imap_host", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("imap_use_ssl", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "smtp_mailboxes",
        sa.Column("imap_use_starttls", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("smtp_mailboxes", sa.Column("imap_username", sa.String(length=320), nullable=True))
    op.add_column("smtp_mailboxes", sa.Column("imap_password_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "smtp_mailboxes",
        sa.Column("imap_sent_folder", sa.String(length=255), nullable=False, server_default=""),
    )

    op.create_table(
        "smtp_sent_copies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("folder", sa.String(length=255), nullable=True),
        sa.Column("uid", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["connection_id"], ["smtp_mailboxes.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_smtp_sent_copies_connection_message",
        "smtp_sent_copies",
        ["connection_id", "message_id"],
        unique=True,
    )
    op.create_index(
        "idx_smtp_sent_copies_status",
        "smtp_sent_copies",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_smtp_sent_copies_status", table_name="smtp_sent_copies")
    op.drop_index("uq_smtp_sent_copies_connection_message", table_name="smtp_sent_copies")
    op.drop_table("smtp_sent_copies")
    for column in (
        "imap_sent_folder",
        "imap_password_encrypted",
        "imap_username",
        "imap_use_starttls",
        "imap_use_ssl",
        "imap_port",
        "imap_host",
        "save_sent_copy",
    ):
        op.drop_column("smtp_mailboxes", column)
