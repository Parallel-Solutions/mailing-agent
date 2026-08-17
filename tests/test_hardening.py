from __future__ import annotations

import shutil
import unittest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from src.infra import object_store
from src.infra.db import _database_name_from_url
from src.jobs.state import _safe_agent_name
from src.jobs.workspace import _safe_local_path
from src.security.passwords import dummy_verify_password, hash_password, verify_password
from tests import bootstrap


class SafeLocalPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("tmp_test_hardening_root").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def test_accepts_normal_relative_path(self) -> None:
        result = _safe_local_path(self.root, "output/1_Test/kp.pdf")
        self.assertTrue(result.is_relative_to(self.root))

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            _safe_local_path(self.root, "../secret.txt")

    def test_rejects_nested_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            _safe_local_path(self.root, "output/../../etc/passwd")

    def test_rejects_empty_path(self) -> None:
        with self.assertRaises(ValueError):
            _safe_local_path(self.root, "")


class SafeAgentNameTests(unittest.TestCase):
    def test_accepts_known_agent_names(self) -> None:
        for name in ("generator", "philologist", "sender", "parser", "documents.details"):
            self.assertEqual(_safe_agent_name(name), name)

    def test_rejects_traversal_and_separators(self) -> None:
        for bad in ("..", ".", "../x", "a/b", "a\\b", "name with space", ""):
            with self.assertRaises(ValueError):
                _safe_agent_name(bad)


class DatabaseNameTests(unittest.TestCase):
    def test_accepts_plain_name(self) -> None:
        self.assertEqual(
            _database_name_from_url("postgresql+psycopg://u:p@host:5432/mailing"),
            "mailing",
        )

    def test_rejects_injection_characters(self) -> None:
        for url in (
            'postgresql://u:p@h/mailing"; DROP',
            "postgresql://u:p@h/mai-ling",
            "postgresql://u:p@h/ma ling",
        ):
            with self.assertRaises(ValueError):
                _database_name_from_url(url)

    def test_rejects_missing_name(self) -> None:
        with self.assertRaises(ValueError):
            _database_name_from_url("postgresql://u:p@host:5432/")


class TestDatabaseResetGuardTests(unittest.TestCase):
    def test_requires_explicit_test_mode(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MAILING_AGENT_TEST_MODE=1"):
                bootstrap.assert_test_database_is_safe()

    def test_rejects_working_database_even_in_test_mode(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "MAILING_AGENT_TEST_MODE": "1",
                    "MAILING_AGENT_TEST_DATABASE": "mailing_test",
                },
                clear=True,
            ),
            patch.object(bootstrap.engine, "url") as engine_url,
        ):
            engine_url.database = "mailing"
            with self.assertRaisesRegex(RuntimeError, "non-test database"):
                bootstrap.assert_test_database_is_safe()

    def test_accepts_explicit_matching_test_database(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "MAILING_AGENT_TEST_MODE": "1",
                    "MAILING_AGENT_TEST_DATABASE": "mailing_test",
                },
                clear=True,
            ),
            patch.object(bootstrap.engine, "url") as engine_url,
        ):
            engine_url.database = "mailing_test"
            bootstrap.assert_test_database_is_safe()


class DatabaseRuntimeGuardTests(unittest.TestCase):
    def test_database_creation_path_validates_runtime_contour_first(self) -> None:
        from src.infra import db

        with (
            patch.object(db, "validate_runtime_database") as validate,
            patch.object(db, "create_engine") as create_engine,
        ):
            db.ensure_database_exists()

        validate.assert_called_once_with(db.settings)
        create_engine.assert_called_once()

    def test_database_creation_stops_when_runtime_contour_is_invalid(self) -> None:
        from src.infra import db

        with (
            patch.object(
                db,
                "validate_runtime_database",
                side_effect=RuntimeError("unsafe contour"),
            ),
            patch.object(db, "create_engine") as create_engine,
        ):
            with self.assertRaisesRegex(RuntimeError, "unsafe contour"):
                db.ensure_database_exists()

        create_engine.assert_not_called()

    def test_migration_lock_is_released_after_failure(self) -> None:
        from src.infra import db

        connection = MagicMock()
        connection.dialect.name = "postgresql"
        mocked_engine = MagicMock()
        mocked_engine.connect.return_value.__enter__.return_value = connection

        with patch.object(db, "engine", mocked_engine):
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                with db._migration_lock():
                    raise RuntimeError("migration failed")

        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertEqual(
            statements,
            [
                "SELECT pg_advisory_lock(:lock_id)",
                "SELECT pg_advisory_unlock(:lock_id)",
            ],
        )

    def test_detects_current_merged_schema_when_stamp_lags(self) -> None:
        from src.infra import db

        with (
            patch.object(
                db,
                "_template_version_column_names",
                return_value=set(db._TEMPLATE_SOURCE_TEXT_COLUMNS),
            ),
            patch.object(
                db,
                "_has_table",
                side_effect=lambda _connection, table: table
                == "user_onboarding_states",
            ),
            # None of the 0032-0050 markers are present in this simulated
            # schema — without this, an unpatched _has_column against a
            # MagicMock connection would return truthy for every call
            # (`.scalar() is not None` on a MagicMock's default chain is
            # always True), short-circuiting on the newest column-based
            # check long before reaching the table-based one this test
            # actually exercises.
            patch.object(db, "_has_column", return_value=False),
        ):
            revision = db._detect_schema_revision(MagicMock())

        self.assertEqual(revision, "0031_merge_onboarding_main")

    def test_detects_main_schema_without_onboarding_branch(self) -> None:
        from src.infra import db

        with (
            patch.object(
                db,
                "_template_version_column_names",
                return_value=set(db._TEMPLATE_SOURCE_TEXT_COLUMNS),
            ),
            patch.object(db, "_has_table", return_value=False),
            patch.object(db, "_has_column", return_value=False),
        ):
            revision = db._detect_schema_revision(MagicMock())

        self.assertEqual(revision, "0030_template_source_text_cache")


class PasswordHardeningTests(unittest.TestCase):
    def test_dummy_verify_password_never_raises(self) -> None:
        self.assertIsNone(dummy_verify_password())

    def test_verify_password_roundtrip(self) -> None:
        hashed = hash_password("s3cret-password")
        self.assertTrue(verify_password("s3cret-password", hashed))
        self.assertFalse(verify_password("wrong", hashed))

    def test_verify_password_handles_empty_hash(self) -> None:
        self.assertFalse(verify_password("anything", ""))


class EnsureBucketTests(unittest.TestCase):
    def _client_error(self, code: str) -> ClientError:
        return ClientError({"Error": {"Code": code}}, "HeadBucket")

    def test_creates_bucket_when_missing(self) -> None:
        client = MagicMock()
        client.head_bucket.side_effect = self._client_error("404")
        with patch.object(object_store, "_s3_client", return_value=client):
            object_store.ensure_bucket()
        client.create_bucket.assert_called_once()

    def test_reraises_permission_error(self) -> None:
        client = MagicMock()
        client.head_bucket.side_effect = self._client_error("403")
        with patch.object(object_store, "_s3_client", return_value=client):
            with self.assertRaises(ClientError):
                object_store.ensure_bucket()
        client.create_bucket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
