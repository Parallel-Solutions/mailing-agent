from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.generator.delivery import consent_store


class ConsentStoreTests(unittest.TestCase):
    def test_prepare_and_confirm_consent(self) -> None:
        temp_root = Path(r"C:\tmp")
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                )

                self.assertIn("/consent/request/", record["consent_url"])
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )

                confirmed = consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")

                self.assertIsNotNone(confirmed)
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )


if __name__ == "__main__":
    unittest.main()
