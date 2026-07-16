from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.migrate.import_sent_mail_stats import (
    _record_idempotency_key,
    import_sent_mail_stats,
)


class ImportSentMailStatsTests(unittest.TestCase):
    def test_idempotency_key_prefers_provider_message_id(self) -> None:
        key = _record_idempotency_key(
            "sent_mail_log",
            {"provider_message_id": "msg-1", "recipient": "a@example.com"},
        )
        self.assertEqual(key, "sent_mail_log:msg-1")

    def test_idempotency_key_falls_back_to_fingerprint(self) -> None:
        key = _record_idempotency_key(
            "sent_mail_log",
            {"recipient": "a@example.com", "row_id": "1", "sent_at": "2026-01-01", "status": "sent"},
        )
        self.assertTrue(key.startswith("sent_mail_log:fp:"))

    def test_import_merge_skips_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp) / "jobs"
            job_dir = jobs_dir / "job-abc123"
            job_dir.mkdir(parents=True)
            (job_dir / "sent_mail_log.jsonl").write_text(
                '{"recipient":"a@example.com","status":"sent","transport":"smtp","sent_at":"2026-01-01T00:00:00"}\n'
                '{"recipient":"a@example.com","status":"sent","transport":"smtp","sent_at":"2026-01-01T00:00:00"}\n',
                encoding="utf-8",
            )

            calls: list[str | None] = []

            def _fake_append(job_id, stream, payload, *, idempotency_key=None):
                calls.append(idempotency_key)
                # First insert ok, second duplicate key
                if calls.count(idempotency_key) > 1:
                    return None
                return len(calls)

            with (
                patch("scripts.migrate.import_sent_mail_stats.init_db"),
                patch("scripts.migrate.import_sent_mail_stats.append_event", side_effect=_fake_append),
                patch("scripts.migrate.import_sent_mail_stats._stream_has_events", return_value=False),
            ):
                report = import_sent_mail_stats(jobs_dirs=[jobs_dir], merge=True)

            self.assertEqual(report["jobs_with_files"], 1)
            self.assertEqual(report["streams"]["sent_mail_log"]["imported"], 1)
            self.assertEqual(report["streams"]["sent_mail_log"]["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
