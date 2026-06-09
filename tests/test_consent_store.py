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
            consents_dir = job_dir / "consents"

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                )

                self.assertIn("/consent/confirm/", record["consent_url"])
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )

                confirmed = consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")

                self.assertIsNotNone(confirmed)
                consent_document_path = Path(confirmed["consent_document_path"])
                if not consent_document_path.is_absolute():
                    consent_document_path = job_dir / consent_document_path
                self.assertTrue(consent_document_path.exists())
                self.assertTrue(consent_document_path.is_file())
                self.assertTrue(consent_document_path.is_relative_to(consents_dir))
                self.assertFalse((job_dir / "output").exists())
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )


if __name__ == "__main__":
    unittest.main()
