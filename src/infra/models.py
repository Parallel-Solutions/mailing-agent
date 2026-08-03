from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db import Base


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    contact_person_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    logo_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    work_types: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CompanyMembership(Base):
    __tablename__ = "company_memberships"

    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), ForeignKey("users.username", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_company_memberships_company", "company_id"),
        Index("idx_company_memberships_username", "username"),
    )


class CompanyDocumentCounter(Base):
    __tablename__ = "company_document_counters"

    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    document_type_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CompanyDocumentNumberAllocation(Base):
    __tablename__ = "company_document_number_allocations"

    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    document_type_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    allocation_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("idx_company_document_allocations_key", "allocation_key"),)

class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), ForeignKey("users.username", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_sessions_username", "username"),
        Index("idx_sessions_expires_at", "expires_at"),
    )


class JobOwner(Base):
    __tablename__ = "job_owners"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentState(Base):
    __tablename__ = "agent_states"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class JobDoc(Base):
    __tablename__ = "job_docs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("uq_job_events_job_stream_seq", "job_id", "stream", "seq", unique=True),
        Index(
            "uq_job_events_idempotency_key",
            "job_id",
            "stream",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class EventStreamCounter(Base):
    __tablename__ = "event_stream_counters"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stream: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BackgroundTask(Base):
    __tablename__ = "background_tasks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "idx_background_tasks_claim",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
        Index("idx_background_tasks_job_created", "job_id", "created_at"),
        Index("idx_background_tasks_lease", "status", "lease_expires_at"),
        Index(
            "uq_background_tasks_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_background_tasks_active_key",
            "active_key",
            unique=True,
            postgresql_where=text("active_key IS NOT NULL"),
        ),
    )


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("idx_clients_job_row", "job_id", "row_index", unique=True),
    )


class ParserRule(Base):
    __tablename__ = "parser_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_value: Mapped[str] = mapped_column(Text, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        Index("idx_parser_rules_domain", "domain"),
    )


class ParserError(Base):
    __tablename__ = "parser_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        Index("idx_parser_errors_url", "url"),
    )


class ParserSourceStat(Base):
    __tablename__ = "parser_source_stats"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_resp_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_success: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_fail: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ParserRunHistory(Base):
    __tablename__ = "parser_run_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False    )


class SuppressionEntry(Base):
    __tablename__ = "suppression_entries"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_suppression_entries_reason", "reason"),
        Index("idx_suppression_entries_expires_at", "expires_at"),
    )


class SendGuardState(Base):
    __tablename__ = "send_guard_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SmtpMailbox(Base):
    __tablename__ = "smtp_mailboxes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    use_starttls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False, default="password")
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_tokens_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(320), nullable=True)
    save_sent_copy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imap_host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imap_use_starttls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    imap_username: Mapped[str | None] = mapped_column(String(320), nullable=True)
    imap_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    imap_sent_folder: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sending_key_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_guard_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivery_error_rate_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    delivery_error_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    delivery_error_min_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    delivery_error_critical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    delivery_error_action: Mapped[str] = mapped_column(String(16), nullable=False, default="warmup")
    delivery_throttled_max_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    delivery_guard_state: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    delivery_guard_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_guard_terminal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_guard_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_guard_error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    delivery_guard_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_guard_last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warmup_recipients: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warmup_percent_of_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    warmup_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    warmup_status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    warmup_sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warmup_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warmup_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warmup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_smtp_mailboxes_owner", "owner_username"),
        Index("idx_smtp_mailboxes_owner_default", "owner_username", "is_default"),
    )


class SmtpSentCopy(Base):
    __tablename__ = "smtp_sent_copies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("smtp_mailboxes.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("uq_smtp_sent_copies_connection_message", "connection_id", "message_id", unique=True),
        Index("idx_smtp_sent_copies_status", "status", "updated_at"),
    )


class DeliveryChannelOutcome(Base):
    __tablename__ = "delivery_channel_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("smtp_mailboxes.id", ondelete="CASCADE"), nullable=False
    )
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    provider_status: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    smtp_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "uq_delivery_channel_outcomes_message",
            "connection_id",
            "provider_message_id",
            unique=True,
        ),
        Index("idx_delivery_channel_outcomes_window", "connection_id", "occurred_at"),
    )


class ConnectionWarmupProgram(Base):
    """User-managed, gradual warmup program for one delivery connection."""

    __tablename__ = "connection_warmup_programs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("smtp_mailboxes.id", ondelete="CASCADE"), nullable=False
    )
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    smtp_connection_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("smtp_mailboxes.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")
    daily_start_time: Mapped[str] = mapped_column(String(5), nullable=False, default="10:00")
    daily_end_time: Mapped[str] = mapped_column(String(5), nullable=False, default="18:00")
    pause_campaigns_during_warmup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_growth_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    current_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    daily_plan: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    diagnostics_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_checked")
    diagnostics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    subject_templates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    body_templates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipients_consent_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recipients_consent_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("uq_connection_warmup_program_connection", "connection_id", unique=True),
        Index("idx_connection_warmup_program_owner", "owner_username"),
    )


class ConnectionWarmupRecipient(Base):
    __tablename__ = "connection_warmup_recipients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    program_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connection_warmup_programs.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("uq_connection_warmup_recipient_email", "program_id", "email", unique=True),
        Index("idx_connection_warmup_recipient_status", "program_id", "status"),
    )


class ConnectionWarmupDelivery(Base):
    __tablename__ = "connection_warmup_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    program_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connection_warmup_programs.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connection_warmup_recipients.id", ondelete="CASCADE"), nullable=False
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_connection_warmup_delivery_program_day", "program_id", "day_number"),
        Index("idx_connection_warmup_delivery_recipient", "recipient_id", "scheduled_at"),
        Index("idx_connection_warmup_delivery_task", "task_id"),
        Index("idx_connection_warmup_delivery_provider_message", "provider_message_id"),
        Index(
            "uq_connection_warmup_delivery_sequence",
            "program_id",
            "run_number",
            "day_number",
            "sequence_number",
            unique=True,
        ),
    )

class DeliveryChannelSendSlot(Base):
    __tablename__ = "delivery_channel_send_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("smtp_mailboxes.id", ondelete="CASCADE"), nullable=False
    )
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_delivery_channel_send_slots_window", "connection_id", "reserved_at"),
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    username: Mapped[str] = mapped_column(String(32), ForeignKey("users.username", ondelete="CASCADE"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    job_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")
    mailing_defaults: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notifications: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserOnboardingState(Base):
    __tablename__ = "user_onboarding_states"

    username: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.username", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Audience(Base):
    __tablename__ = "audiences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_audiences_owner", "owner_username"),)


class AudienceMember(Base):
    __tablename__ = "audience_members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    audience_id: Mapped[str] = mapped_column(String(36), ForeignKey("audiences.id", ondelete="CASCADE"), nullable=False)
    company: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    email_fallback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    region: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_audience_members_audience", "audience_id"),
        Index("idx_audience_members_email", "audience_id", "email"),
    )


class FontAsset(Base):
    __tablename__ = "font_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    family: Mapped[str] = mapped_column(String(255), nullable=False)
    family_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    subfamily: Mapped[str] = mapped_column(String(128), nullable=False, default="Regular")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=400)
    italic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    postscript_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    license_type: Mapped[str] = mapped_column(String(64), nullable=False, default="user_confirmed")
    license_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    license_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    embedding_permissions: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    glyph_coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_font_assets_owner_family", "owner_username", "family_normalized", "status"),
        Index("uq_font_assets_owner_sha256", "owner_username", "sha256", unique=True),
    )


class MailTemplate(Base):
    __tablename__ = "mail_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[str] = mapped_column(String(32), nullable=False)  # email | kp | contract
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attachment_output_format: Mapped[str] = mapped_column(
        String(16), nullable=False, default="original"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_mail_templates_owner_type", "owner_username", "template_type"),
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(36), ForeignKey("mail_templates.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subject: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rendered_pdf_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rendered_pdf_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    editor_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text_extraction_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    text_extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_template_versions_template", "template_id", "version_number", unique=True),
    )


class TemplateFontRequirement(Base):
    __tablename__ = "template_font_requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_versions.id", ondelete="CASCADE"), nullable=False
    )
    family: Mapped[str] = mapped_column(String(255), nullable=False)
    family_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=400)
    italic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_font_asset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("font_assets.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="document")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    fallback_family: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_template_font_requirements_version", "template_version_id"),
        Index(
            "uq_template_font_requirement_signature",
            "template_version_id",
            "family_normalized",
            "weight",
            "italic",
            unique=True,
        ),
    )


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    work_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    document_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="kp")  # kp | contract | both
    mail_subject: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    send_scenario: Mapped[str] = mapped_column(String(64), nullable=False, default="consent_then_materials")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    internal_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    smtp_mailbox_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    connection_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="smtp")
    email_template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kp_template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    contract_template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    audience_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    email_chain_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("email_chains.id", ondelete="SET NULL"), nullable=True
    )
    draft_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_campaigns_owner_status", "owner_username", "status"),
        Index("idx_campaigns_job_id", "job_id"),
    )


class CampaignStatusEvent(Base):
    __tablename__ = "campaign_status_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("idx_campaign_status_events_campaign_created", "campaign_id", "created_at"),
    )


class EmailChainRecord(Base):
    __tablename__ = "email_chains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_email_chains_owner", "owner_username"),)


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    company: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    email_fallback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    region: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    send_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_campaign_recipients_campaign", "campaign_id", "row_index", unique=True),
        Index("idx_campaign_recipients_email", "campaign_id", "email"),
    )


class CampaignSchedule(Base):
    __tablename__ = "campaign_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True)
    send_immediately: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")
    weekdays: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # 0=Mon .. 6=Sun
    time_windows: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # [{start,end}]
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    pause_between_messages_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_error: Mapped[str] = mapped_column(String(32), nullable=False, default="skip")  # retry | skip | pause
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    preview: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CampaignBatch(Base):
    __tablename__ = "campaign_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recipient_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_campaign_batches_campaign", "campaign_id", "batch_index", unique=True),
        Index("idx_campaign_batches_status", "campaign_id", "status"),
    )


class CampaignChainConsentEvent(Base):
    __tablename__ = "campaign_chain_consent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    edge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_chain_consent_campaign_action", "campaign_id", "action"),
        Index("idx_chain_consent_email_action_expires", "email", "action", "expires_at"),
        Index("idx_chain_consent_token", "token", unique=True),
    )


class CampaignChainToken(Base):
    __tablename__ = "campaign_chain_tokens"

    token: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    edge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    send_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        Index("idx_chain_tokens_campaign", "campaign_id"),
        Index("idx_chain_tokens_recipient", "campaign_id", "recipient_id"),
        Index("idx_chain_tokens_edge", "campaign_id", "edge_id"),
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("uq_delivery_attempts_idempotency", "idempotency_key", unique=True),
        Index("idx_delivery_attempts_campaign", "campaign_id", "recipient_id"),
    )
