from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.utils.config import settings, validate_runtime_database


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_SAFE_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MIGRATION_ADVISORY_LOCK_ID = 748_106_283
_DELIVERY_GUARD_COLUMNS = {
    "delivery_guard_enabled",
    "delivery_error_rate_threshold",
    "delivery_error_window_minutes",
    "delivery_error_min_samples",
    "delivery_error_critical_count",
    "delivery_error_action",
    "delivery_throttled_max_per_hour",
    "delivery_guard_state",
    "delivery_guard_reason",
    "delivery_guard_terminal_count",
    "delivery_guard_error_count",
    "delivery_guard_error_rate",
    "delivery_guard_triggered_at",
    "delivery_guard_last_error_at",
}
_WARMUP_COLUMNS = {
    "warmup_recipients",
    "warmup_percent_of_errors",
    "warmup_task_id",
    "warmup_status",
    "warmup_sent_count",
    "warmup_error_count",
    "warmup_started_at",
    "warmup_completed_at",
}
_TEMPLATE_SOURCE_TEXT_COLUMNS = {
    "source_text",
    "source_sha256",
    "text_extraction_status",
    "text_extraction_error",
    "text_extracted_at",
}


def _database_name_from_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    database_name = unquote((parsed.path or "").lstrip("/"))
    if not database_name:
        raise ValueError("DATABASE_URL must include a database name.")
    if not _SAFE_DB_NAME_RE.match(database_name):
        raise ValueError(
            "Database name must contain only letters, digits and underscores; "
            f"got {database_name!r}."
        )
    return database_name


def _admin_database_url(database_url: str, *, admin_database: str = "postgres") -> str:
    parsed = urlparse(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    normalized_path = f"/{admin_database.lstrip('/')}"
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )


def ensure_database_exists() -> None:
    # Every entrypoint (API, worker, parser and one-shot migration scripts)
    # reaches the database through this function. Keep the contour guard here
    # so a maintenance command cannot bypass checks performed by main.py.
    validate_runtime_database(settings)
    database_url = settings.database_url
    database_name = _database_name_from_url(database_url)
    admin_engine = create_engine(
        _admin_database_url(database_url),
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()


def check_db_connection() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


@contextmanager
def _migration_lock() -> Iterator[None]:
    """Serialize Alembic work performed by independently starting runtimes."""
    with engine.connect() as connection:
        if connection.dialect.name != "postgresql":
            yield
            return
        connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": _MIGRATION_ADVISORY_LOCK_ID},
        )
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _MIGRATION_ADVISORY_LOCK_ID},
            )


_LEGACY_ORPHAN_REVISIONS = {
    # Old image heads from before template PDF/editor migrations were inserted.
    "0007_smtp_mailbox_rate_limits": "0009_smtp_mailbox_rate_limits",
    "0008_document_template_type": "0010_document_template_type",
}


def _template_version_column_names(connection) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'template_versions'"
        )
    )
    return {str(row[0]) for row in rows}


def _sync_missing_template_version_columns(connection) -> None:
    columns = _template_version_column_names(connection)
    if "rendered_pdf_storage_key" not in columns:
        connection.execute(
            text("ALTER TABLE template_versions ADD COLUMN rendered_pdf_storage_key VARCHAR(512)")
        )
    if "rendered_pdf_filename" not in columns:
        connection.execute(
            text("ALTER TABLE template_versions ADD COLUMN rendered_pdf_filename VARCHAR(255)")
        )
    if "editor_state" not in columns:
        connection.execute(text("ALTER TABLE template_versions ADD COLUMN editor_state JSONB"))


def _sync_missing_campaign_connection_ids(connection) -> None:
    if not _has_table(connection, "campaigns"):
        return
    if _has_column(connection, "campaigns", "connection_ids"):
        return
    connection.execute(
        text(
            "ALTER TABLE campaigns ADD COLUMN connection_ids JSONB NOT NULL "
            "DEFAULT '[]'::jsonb"
        )
    )
    if _has_column(connection, "campaigns", "smtp_mailbox_id"):
        connection.execute(
            text(
                """
                UPDATE campaigns
                SET connection_ids = jsonb_build_array(smtp_mailbox_id)
                WHERE smtp_mailbox_id IS NOT NULL
                """
            )
        )


def _recover_orphaned_alembic_revision(connection) -> str | None:
    current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if current in _LEGACY_ORPHAN_REVISIONS:
        _sync_missing_template_version_columns(connection)
        stamp_to = _LEGACY_ORPHAN_REVISIONS[str(current)]
        connection.execute(text("UPDATE alembic_version SET version_num = :rev"), {"rev": stamp_to})
        return stamp_to
    return None


def _has_table(connection, name: str) -> bool:
    return (
        connection.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{name}"}).scalar()
        is not None
    )


def _has_column(connection, table: str, column: str) -> bool:
    return (
        connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar()
        is not None
    )


def _column_default_contains(connection, table: str, column: str, fragment: str) -> bool:
    default = connection.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).scalar()
    return bool(default and fragment in str(default))


def _column_data_type(connection, table: str, column: str) -> str | None:
    value = connection.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).scalar()
    return str(value) if value is not None else None


def _has_columns(connection, table: str, columns: set[str]) -> bool:
    return all(_has_column(connection, table, column) for column in columns)


def _mail_template_column_names(connection) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'mail_templates'"
        )
    )
    return {str(row[0]) for row in rows}


def _detect_schema_revision(connection) -> str | None:
    """Best-effort stamp when alembic_version lags behind the real schema."""
    template_version_columns = _template_version_column_names(connection)
    has_template_source_text_cache = _TEMPLATE_SOURCE_TEXT_COLUMNS.issubset(
        template_version_columns
    )
    if has_template_source_text_cache and _has_table(
        connection, "user_onboarding_states"
    ):
        return "0031_merge_onboarding_main"
    if has_template_source_text_cache:
        return "0030_template_source_text_cache"
    if (
        _column_data_type(connection, "audience_members", "email_fallback") == "text"
        and _column_data_type(connection, "campaign_recipients", "email_fallback")
        == "text"
    ):
        return "0029_fallback_email_text"
    if _has_table(connection, "font_assets") and _has_table(
        connection, "template_font_requirements"
    ):
        return "0028_font_assets"
    if _has_column(connection, "mail_templates", "attachment_output_format"):
        return "0027_template_attachment_format"
    if _has_columns(connection, "smtp_mailboxes", _WARMUP_COLUMNS):
        return "0026_connection_error_warmup"
    if _has_table(connection, "campaign_status_events"):
        return "0025_campaign_status_events"
    if (
        _has_table(connection, "delivery_channel_outcomes")
        and _has_table(connection, "delivery_channel_send_slots")
        and _has_columns(connection, "smtp_mailboxes", _DELIVERY_GUARD_COLUMNS)
    ):
        return "0024_delivery_channel_guard"
    if _has_column(connection, "campaign_batches", "worker_recovery_count"):
        return "0023_campaign_batch_recovery"
    if _has_column(connection, "delivery_attempts", "delivery_email"):
        return "0022_delivery_attempt_email"
    if _has_column(connection, "campaign_chain_tokens", "test_email"):
        return "0021_chain_token_test_email"
    if _has_table(connection, "company_document_counters"):
        return "0020_company_document_numbers"
    if _has_column(connection, "companies", "work_types"):
        return "0019_company_work_types"
    if _column_default_contains(connection, "campaign_schedules", "on_error", "skip"):
        return "0017_on_error_skip"
    if _has_table(connection, "companies"):
        if not _has_column(connection, "campaigns", "connection_ids"):
            return "0015_mail_template_is_template"
        return "0016_companies"
    mail_template_columns = _mail_template_column_names(connection)
    if "is_template" in mail_template_columns:
        return "0015_mail_template_is_template"
    if _has_table(connection, "email_chains"):
        return "0014_standalone_email_chains"
    if _has_table(connection, "campaign_chain_consent_events"):
        return "0013_chain_consent_events"
    if _has_table(connection, "campaign_chain_tokens"):
        return "0012_email_chain"
    if (
        "editor_state" in template_version_columns
        or "rendered_pdf_storage_key" in template_version_columns
    ):
        return "0011_template_version_cols"
    if _has_table(connection, "mail_templates"):
        return "0006_campaign_flow_domain"
    if _has_table(connection, "smtp_mailboxes"):
        return "0005_smtp_mailbox_oauth"
    if _has_table(connection, "users"):
        return "0002_durable_events_and_tasks"
    return None


def init_db() -> None:
    """Ensure target database exists and apply Alembic migrations to head."""
    ensure_database_exists()
    with _migration_lock():
        with engine.begin() as connection:
            _sync_missing_campaign_connection_ids(connection)
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        try:
            command.upgrade(alembic_cfg, "head")
        except Exception as exc:
            message = str(exc)
            recoverable = (
                "Can't locate revision" in message
                or "DuplicateTable" in message
                or "already exists" in message
            )
            if not recoverable:
                raise
            # Recover DBs stamped with orphaned revision ids from older branches.
            with engine.begin() as connection:
                users_table = connection.execute(
                    text("SELECT to_regclass('public.users')")
                ).scalar()
                if not users_table:
                    raise
                if _recover_orphaned_alembic_revision(connection) is not None:
                    command.upgrade(alembic_cfg, "head")
                    return
                detected = _detect_schema_revision(connection)
                if detected:
                    connection.execute(
                        text("UPDATE alembic_version SET version_num = :rev"),
                        {"rev": detected},
                    )
                else:
                    smtp_table = connection.execute(
                        text("SELECT to_regclass('public.smtp_mailboxes')")
                    ).scalar()
                    stamp_to = (
                        "0005_smtp_mailbox_oauth"
                        if smtp_table
                        else "0002_durable_events_and_tasks"
                    )
                    connection.execute(
                        text("UPDATE alembic_version SET version_num = :rev"),
                        {"rev": stamp_to},
                    )
            command.upgrade(alembic_cfg, "head")
