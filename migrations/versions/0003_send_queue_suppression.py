"""Add suppression list and send guard state.

Revision ID: 0003_send_queue_suppression
Revises: 0002_durable_events_and_tasks
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_send_queue_suppression"
down_revision = "0002_durable_events_and_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "suppression_entries" not in existing:
        op.create_table(
            "suppression_entries",
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("reason", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("email"),
        )
        op.create_index("idx_suppression_entries_reason", "suppression_entries", ["reason"], unique=False)
        op.create_index("idx_suppression_entries_expires_at", "suppression_entries", ["expires_at"], unique=False)

    if "send_guard_state" not in existing:
        op.create_table(
            "send_guard_state",
            sa.Column("id", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("pause_reason", sa.Text(), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
        )
        op.execute(
            "INSERT INTO send_guard_state (id, paused, pause_reason, paused_at, updated_at) "
            "VALUES (1, false, NULL, NULL, now())"
        )


def downgrade() -> None:
    op.drop_table("send_guard_state")
    op.drop_table("suppression_entries")
