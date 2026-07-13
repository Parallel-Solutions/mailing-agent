"""Make event streams atomic and add a durable background-task queue.

Revision ID: 0002_durable_events_and_tasks
Revises: 0001_initial
Create Date: 2026-07-13
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
    op.add_column("job_events", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY job_id, stream
                    ORDER BY seq, created_at, id
                ) AS new_seq
            FROM job_events
        )
        UPDATE job_events AS event
        SET seq = ranked.new_seq
        FROM ranked
        WHERE event.id = ranked.id
          AND event.seq <> ranked.new_seq
        """
    )
    op.drop_index("idx_job_events_job_stream_seq", table_name="job_events")
    op.create_index(
        "uq_job_events_job_stream_seq",
        "job_events",
        ["job_id", "stream", "seq"],
        unique=True,
    )
    op.create_index(
        "uq_job_events_idempotency_key",
        "job_events",
        ["job_id", "stream", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "event_stream_counters",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "stream"),
    )
    op.execute(
        """
        INSERT INTO event_stream_counters (job_id, stream, last_seq, updated_at)
        SELECT job_id, stream, max(seq), now()
        FROM job_events
        GROUP BY job_id, stream
        """
    )

    op.create_table(
        "background_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("owner_username", sa.String(length=32), server_default="", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("active_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_background_tasks_claim",
        "background_tasks",
        ["status", "available_at", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_background_tasks_job_created",
        "background_tasks",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_background_tasks_lease",
        "background_tasks",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_background_tasks_idempotency_key",
        "background_tasks",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_background_tasks_active_key",
        "background_tasks",
        ["active_key"],
        unique=True,
        postgresql_where=sa.text("active_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_tasks_active_key", table_name="background_tasks")
    op.drop_index("uq_background_tasks_idempotency_key", table_name="background_tasks")
    op.drop_index("idx_background_tasks_lease", table_name="background_tasks")
    op.drop_index("idx_background_tasks_job_created", table_name="background_tasks")
    op.drop_index("idx_background_tasks_claim", table_name="background_tasks")
    op.drop_table("background_tasks")

    op.drop_table("event_stream_counters")
    op.drop_index("uq_job_events_idempotency_key", table_name="job_events")
    op.drop_index("uq_job_events_job_stream_seq", table_name="job_events")
    op.create_index(
        "idx_job_events_job_stream_seq",
        "job_events",
        ["job_id", "stream", "seq"],
        unique=False,
    )
    op.drop_column("job_events", "idempotency_key")
