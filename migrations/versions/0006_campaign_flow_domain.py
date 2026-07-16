"""CampaignFlow domain tables: profiles, audiences, templates, campaigns, batches.

Revision ID: 0006_campaign_flow_domain
Revises: 0005_smtp_mailbox_oauth
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_campaign_flow_domain"
down_revision = "0005_smtp_mailbox_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("username", sa.String(length=32), sa.ForeignKey("users.username", ondelete="CASCADE"), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("company", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("job_title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("mailing_defaults", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notifications", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "audiences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_audiences_owner", "audiences", ["owner_username"])

    op.create_table(
        "audience_members",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("audience_id", sa.String(length=36), sa.ForeignKey("audiences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("email_fallback", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("region", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("validation_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_audience_members_audience", "audience_members", ["audience_id"])
    op.create_index("idx_audience_members_email", "audience_members", ["audience_id", "email"])

    op.create_table(
        "mail_templates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("template_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("active_version_id", sa.String(length=36), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_mail_templates_owner_type", "mail_templates", ["owner_username", "template_type"])

    op.create_table(
        "template_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("template_id", sa.String(length=36), sa.ForeignKey("mail_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("subject", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_template_versions_template", "template_versions", ["template_id", "version_number"], unique=True)

    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_username", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("work_type", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("document_mode", sa.String(length=32), nullable=False, server_default="kp"),
        sa.Column("mail_subject", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("send_scenario", sa.String(length=64), nullable=False, server_default="consent_then_materials"),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("internal_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("smtp_mailbox_id", sa.String(length=36), nullable=True),
        sa.Column("transport", sa.String(length=32), nullable=False, server_default="smtp"),
        sa.Column("email_template_id", sa.String(length=36), nullable=True),
        sa.Column("kp_template_id", sa.String(length=36), nullable=True),
        sa.Column("contract_template_id", sa.String(length=36), nullable=True),
        sa.Column("audience_id", sa.String(length=36), nullable=True),
        sa.Column("draft_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_campaigns_owner_status", "campaigns", ["owner_username", "status"])
    op.create_index("idx_campaigns_job_id", "campaigns", ["job_id"])

    op.create_table(
        "campaign_recipients",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("company", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("email_fallback", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("region", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("validation_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("send_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_campaign_recipients_campaign", "campaign_recipients", ["campaign_id", "row_index"], unique=True)
    op.create_index("idx_campaign_recipients_email", "campaign_recipients", ["campaign_id", "email"])

    op.create_table(
        "campaign_schedules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("send_immediately", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("weekdays", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("time_windows", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("pause_between_messages_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_per_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_per_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("on_error", sa.String(length=32), nullable=False, server_default="retry"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("campaign_id"),
    )

    op.create_table(
        "campaign_batches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("recipient_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_campaign_batches_campaign", "campaign_batches", ["campaign_id", "batch_index"], unique=True)
    op.create_index("idx_campaign_batches_status", "campaign_batches", ["campaign_id", "status"])

    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("uq_delivery_attempts_idempotency", "delivery_attempts", ["idempotency_key"], unique=True)
    op.create_index("idx_delivery_attempts_campaign", "delivery_attempts", ["campaign_id", "recipient_id"])


def downgrade() -> None:
    op.drop_table("delivery_attempts")
    op.drop_table("campaign_batches")
    op.drop_table("campaign_schedules")
    op.drop_table("campaign_recipients")
    op.drop_table("campaigns")
    op.drop_table("template_versions")
    op.drop_table("mail_templates")
    op.drop_table("audience_members")
    op.drop_table("audiences")
    op.drop_table("user_profiles")
