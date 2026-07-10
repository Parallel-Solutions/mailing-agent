from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from src.infra import object_store
from src.infra.db import _database_name_from_url
from src.jobs.state import _safe_agent_name
from src.jobs.workspace import _safe_local_path
from src.security.passwords import dummy_verify_password, hash_password, verify_password


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
