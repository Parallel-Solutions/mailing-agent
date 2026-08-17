"""Regression tests for Alembic recovery and schema sync helpers."""

from __future__ import annotations

import unittest

from sqlalchemy import text

from src.infra.db import (
    _detect_schema_revision,
    _has_column,
    _sync_missing_campaign_connection_ids,
    engine,
)
from tests.bootstrap import bootstrap_test_runtime


class DbMigrationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)

    def test_sync_missing_campaign_connection_ids_adds_column(self) -> None:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE campaigns DROP COLUMN IF EXISTS connection_ids"))

        with engine.begin() as connection:
            self.assertFalse(_has_column(connection, "campaigns", "connection_ids"))
            _sync_missing_campaign_connection_ids(connection)

        with engine.begin() as connection:
            self.assertTrue(_has_column(connection, "campaigns", "connection_ids"))

    def test_sync_missing_campaign_connection_ids_is_idempotent(self) -> None:
        with engine.begin() as connection:
            _sync_missing_campaign_connection_ids(connection)
            _sync_missing_campaign_connection_ids(connection)

        with engine.begin() as connection:
            self.assertTrue(_has_column(connection, "campaigns", "connection_ids"))

    def test_detect_schema_revision_recognizes_current_head_schema(self) -> None:
        """A fully migrated DB hitting the DuplicateTable/already-exists
        recovery path must be detected as being at (or past) the real head,
        not fall back to the previous ceiling of 0031_merge_onboarding_main
        — which would roll alembic_version backwards and re-run already
        applied migrations."""
        with engine.begin() as connection:
            detected = _detect_schema_revision(connection)
        self.assertEqual(detected, "0046_merge_smtpbz_spends")

    def test_detect_schema_revision_recognizes_intermediate_schema(self) -> None:
        """Simulate a DB stopped partway between 0037 and 0046: markers for
        0038-0046 removed, 0037's marker (still) present. Detection must
        land on 0037, not skip past it or fall all the way back to 0031.

        Runs inside one uncommitted transaction, rolled back at the end, so
        this test's destructive DDL never leaks into the shared test
        database for other tests."""
        connection = engine.connect()
        try:
            connection.execute(text("DROP TABLE IF EXISTS external_service_spends CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS email_validation_runs CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS email_validation_cache CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS delivery_key_guards CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS company_access_grants CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS smtp_open_tracking CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS smtp_sent_copies CASCADE"))
            connection.execute(
                text(
                    "ALTER TABLE smtp_mailboxes DROP COLUMN IF EXISTS "
                    "delivery_guard_monitoring_started_at"
                )
            )
            detected = _detect_schema_revision(connection)
            self.assertEqual(detected, "0037_fix_warmup_volume")
        finally:
            connection.rollback()
            connection.close()
