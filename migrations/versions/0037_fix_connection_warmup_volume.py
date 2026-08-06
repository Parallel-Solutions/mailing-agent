"""Allow repeated warmup deliveries and require an SMTP sender.

Revision ID: 0037_fix_warmup_volume
Revises: 0036_connection_warmup_runs
Create Date: 2026-08-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0037_fix_warmup_volume"
down_revision = "0036_connection_warmup_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connection_warmup_programs",
        sa.Column("smtp_connection_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_connection_warmup_program_smtp_connection",
        "connection_warmup_programs",
        "smtp_mailboxes",
        ["smtp_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE connection_warmup_programs AS program
        SET smtp_connection_id = program.connection_id
        FROM smtp_mailboxes AS connection
        WHERE connection.id = program.connection_id
          AND connection.provider NOT IN ('rusender', 'mailopost')
        """
    )
    op.execute(
        """
        UPDATE connection_warmup_programs
        SET status = CASE WHEN status = 'running' THEN 'paused' ELSE status END,
            pause_reason = CASE
                WHEN status = 'running' THEN 'Выберите SMTP-подключение и повторите техническую проверку.'
                ELSE pause_reason
            END,
            diagnostics_status = 'not_checked',
            diagnostics = '{}'::jsonb,
            scheduled_task_id = NULL
        WHERE smtp_connection_id IS NULL
        """
    )

    op.add_column(
        "connection_warmup_deliveries",
        sa.Column("sequence_number", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH numbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY program_id, run_number, day_number
                       ORDER BY created_at, id
                   ) AS sequence_number
            FROM connection_warmup_deliveries
        )
        UPDATE connection_warmup_deliveries AS delivery
        SET sequence_number = numbered.sequence_number
        FROM numbered
        WHERE delivery.id = numbered.id
        """
    )
    op.alter_column("connection_warmup_deliveries", "sequence_number", nullable=False)
    op.drop_index(
        "uq_connection_warmup_delivery_recipient_day",
        table_name="connection_warmup_deliveries",
    )
    op.create_index(
        "uq_connection_warmup_delivery_sequence",
        "connection_warmup_deliveries",
        ["program_id", "run_number", "day_number", "sequence_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_connection_warmup_delivery_sequence",
        table_name="connection_warmup_deliveries",
    )
    op.create_index(
        "uq_connection_warmup_delivery_recipient_day",
        "connection_warmup_deliveries",
        ["program_id", "recipient_id", "run_number", "day_number"],
        unique=True,
    )
    op.drop_column("connection_warmup_deliveries", "sequence_number")
    op.drop_constraint(
        "fk_connection_warmup_program_smtp_connection",
        "connection_warmup_programs",
        type_="foreignkey",
    )
    op.drop_column("connection_warmup_programs", "smtp_connection_id")
