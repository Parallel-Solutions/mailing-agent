"""Make job event streams atomic after the gotenberg queue migration.

Revision ID: 0003_atomic_job_events
Revises: 0002_durable_events_and_tasks
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_atomic_job_events"
down_revision = "0002_durable_events_and_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_events", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY job_id, stream ORDER BY seq, created_at, id
            ) AS new_seq
            FROM job_events
        )
        UPDATE job_events AS event
        SET seq = ranked.new_seq
        FROM ranked
        WHERE event.id = ranked.id AND event.seq <> ranked.new_seq
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


def downgrade() -> None:
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
