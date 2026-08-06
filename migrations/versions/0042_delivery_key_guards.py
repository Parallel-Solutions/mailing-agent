"""Add provider-key scoped delivery guards.

Revision ID: 0042_delivery_key_guards
Revises: 0041_company_access_grants
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0042_delivery_key_guards"
down_revision = "0041_company_access_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_key_guards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_key_id", sa.String(length=128), nullable=False),
        sa.Column("delivery_guard_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delivery_error_rate_threshold", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("delivery_error_window_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("delivery_error_min_samples", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("delivery_error_critical_count", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("delivery_error_action", sa.String(length=16), nullable=False, server_default="warmup"),
        sa.Column("delivery_throttled_max_per_hour", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("delivery_guard_state", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("delivery_guard_reason", sa.Text(), nullable=True),
        sa.Column("delivery_guard_terminal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_guard_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_guard_error_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("delivery_guard_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_guard_last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "warmup_recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("warmup_percent_of_errors", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("warmup_connection_id", sa.String(length=36), nullable=True),
        sa.Column("warmup_task_id", sa.String(length=36), nullable=True),
        sa.Column("warmup_status", sa.String(length=24), nullable=False, server_default="idle"),
        sa.Column("warmup_sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warmup_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warmup_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warmup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["warmup_connection_id"], ["smtp_mailboxes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_delivery_key_guards_scope",
        "delivery_key_guards",
        ["owner_username", "provider", "external_key_id"],
        unique=True,
    )
    op.create_index(
        "idx_delivery_key_guards_state",
        "delivery_key_guards",
        ["delivery_guard_state", "updated_at"],
    )

    op.add_column(
        "delivery_channel_outcomes",
        sa.Column("delivery_key_guard_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_delivery_channel_outcomes_key_guard",
        "delivery_channel_outcomes",
        "delivery_key_guards",
        ["delivery_key_guard_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_delivery_key_outcomes_message",
        "delivery_channel_outcomes",
        ["delivery_key_guard_id", "provider_message_id"],
        unique=True,
    )
    op.create_index(
        "idx_delivery_key_outcomes_window",
        "delivery_channel_outcomes",
        ["delivery_key_guard_id", "occurred_at"],
    )

    # Build one guard for every existing RuSender key. Configuration is
    # deliberately copied from the most recently edited connection, while
    # activation is preserved if any connection in the key scope was enabled.
    op.execute(
        """
        INSERT INTO delivery_key_guards (
            id, owner_username, provider, external_key_id,
            delivery_guard_enabled, delivery_error_rate_threshold,
            delivery_error_window_minutes, delivery_error_min_samples,
            delivery_error_critical_count, delivery_error_action,
            delivery_throttled_max_per_hour, warmup_recipients,
            warmup_percent_of_errors, created_at, updated_at
        )
        SELECT
            'rsg-' || substr(md5(owner_username || ':rusender:' || sending_key_id::text), 1, 32),
            owner_username,
            'rusender',
            sending_key_id::text,
            bool_or(delivery_guard_enabled),
            (array_agg(delivery_error_rate_threshold ORDER BY updated_at DESC))[1],
            (array_agg(delivery_error_window_minutes ORDER BY updated_at DESC))[1],
            (array_agg(delivery_error_min_samples ORDER BY updated_at DESC))[1],
            (array_agg(delivery_error_critical_count ORDER BY updated_at DESC))[1],
            (array_agg(delivery_error_action ORDER BY updated_at DESC))[1],
            (array_agg(delivery_throttled_max_per_hour ORDER BY updated_at DESC))[1],
            (array_agg(warmup_recipients ORDER BY updated_at DESC))[1],
            (array_agg(warmup_percent_of_errors ORDER BY updated_at DESC))[1],
            now(),
            now()
        FROM smtp_mailboxes
        WHERE lower(provider) = 'rusender'
          AND sending_key_id IS NOT NULL
        GROUP BY owner_username, sending_key_id
        ON CONFLICT (owner_username, provider, external_key_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE delivery_channel_outcomes AS outcome
        SET delivery_key_guard_id = guard.id
        FROM smtp_mailboxes AS connection
        JOIN delivery_key_guards AS guard
          ON guard.owner_username = connection.owner_username
         AND guard.provider = 'rusender'
         AND guard.external_key_id = connection.sending_key_id::text
        WHERE outcome.connection_id = connection.id
          AND outcome.delivery_key_guard_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("idx_delivery_key_outcomes_window", table_name="delivery_channel_outcomes")
    op.drop_index("uq_delivery_key_outcomes_message", table_name="delivery_channel_outcomes")
    op.drop_constraint(
        "fk_delivery_channel_outcomes_key_guard",
        "delivery_channel_outcomes",
        type_="foreignkey",
    )
    op.drop_column("delivery_channel_outcomes", "delivery_key_guard_id")
    op.drop_index("idx_delivery_key_guards_state", table_name="delivery_key_guards")
    op.drop_index("uq_delivery_key_guards_scope", table_name="delivery_key_guards")
    op.drop_table("delivery_key_guards")
