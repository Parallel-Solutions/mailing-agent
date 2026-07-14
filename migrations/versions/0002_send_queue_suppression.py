"""Send queue, suppression list, and send guard state.

Revision ID: 0002_send_queue_suppression
Revises: 0001_initial
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_durable_events_and_tasks"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "background_tasks" in existing:
        op.drop_table("background_tasks")
        existing.discard("background_tasks")

    op.create_table(
        "background_tasks",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("owner_username", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("worker_id", sa.String(length=128), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_background_tasks_type_status_created",
        "background_tasks",
        ["task_type", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_background_tasks_job_type_status",
        "background_tasks",
        ["job_id", "task_type", "status"],
        unique=False,
    )

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
    op.drop_index("idx_background_tasks_job_type_status", table_name="background_tasks")
    op.drop_index("idx_background_tasks_type_status_created", table_name="background_tasks")
    op.drop_table("background_tasks")
