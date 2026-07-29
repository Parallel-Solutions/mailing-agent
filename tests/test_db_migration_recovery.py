"""Regression tests for Alembic recovery and schema sync helpers."""

from __future__ import annotations

import unittest

from sqlalchemy import text

from src.infra.db import (
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
