"""Store the onboarding progress for each user.

Revision ID: 0016_user_onboarding_state
Revises: 0015_mail_template_is_template
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_user_onboarding_state"
down_revision = "0015_mail_template_is_template"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_onboarding_states",
        sa.Column(
            "username",
            sa.String(length=32),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completed_steps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'dismissed', 'completed')",
            name="ck_user_onboarding_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_onboarding_states")
