"""Drop the account-wide send guard: per-connection warmup (channel_guard.py)
already isolates and self-heals on error spikes for that connection, so the
blanket send_guard_state pause was redundant and, given its low trigger
threshold, too aggressive — see src/generator/delivery/send_guard.py removal.

Revision ID: 0050_drop_send_guard_state
Revises: 0049_template_page_limit
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "0050_drop_send_guard_state"
down_revision = "0049_template_page_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "send_guard_state" in set(inspector.get_table_names()):
        op.drop_table("send_guard_state")


def downgrade() -> None:
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
