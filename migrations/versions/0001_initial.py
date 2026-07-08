"""Initial schema for PostgreSQL migration.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "sessions",
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("idx_sessions_username", "sessions", ["username"], unique=False)
    op.create_index("idx_sessions_expires_at", "sessions", ["expires_at"], unique=False)

    op.create_table(
        "job_owners",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_table(
        "agent_states",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "agent_name"),
    )
    op.create_table(
        "job_docs",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "name"),
    )
    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_job_events_job_stream_seq", "job_events", ["job_id", "stream", "seq"], unique=False)

    op.create_table(
        "clients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_clients_job_row", "clients", ["job_id", "row_index"], unique=True)

    op.create_table(
        "parser_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("rule_type", sa.String(length=64), nullable=False),
        sa.Column("rule_value", sa.Text(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_parser_rules_domain", "parser_rules", ["domain"], unique=False)

    op.create_table(
        "parser_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("tool", sa.String(length=128), nullable=True),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("solution", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_parser_errors_url", "parser_errors", ["url"], unique=False)

    op.create_table(
        "parser_source_stats",
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False),
        sa.Column("success_runs", sa.Integer(), nullable=False),
        sa.Column("fail_runs", sa.Integer(), nullable=False),
        sa.Column("avg_resp_ms", sa.Float(), nullable=False),
        sa.Column("last_success", sa.String(length=32), nullable=True),
        sa.Column("last_fail", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("domain"),
    )
    op.create_table(
        "parser_run_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("tools_used", sa.Text(), nullable=True),
        sa.Column("records_out", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("parser_run_history")
    op.drop_table("parser_source_stats")
    op.drop_index("idx_parser_errors_url", table_name="parser_errors")
    op.drop_table("parser_errors")
    op.drop_index("idx_parser_rules_domain", table_name="parser_rules")
    op.drop_table("parser_rules")
    op.drop_index("idx_clients_job_row", table_name="clients")
    op.drop_table("clients")
    op.drop_index("idx_job_events_job_stream_seq", table_name="job_events")
    op.drop_table("job_events")
    op.drop_table("job_docs")
    op.drop_table("agent_states")
    op.drop_table("job_owners")
    op.drop_index("idx_sessions_expires_at", table_name="sessions")
    op.drop_index("idx_sessions_username", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
