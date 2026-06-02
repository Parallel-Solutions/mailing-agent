from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.generator.delivery import unisender_go_events


EVENT_LOG = Path(__file__).with_name("_unisender_go_events_test.jsonl")


class UnisenderGoEventsTests(unittest.TestCase):
    def tearDown(self) -> None:
        EVENT_LOG.unlink(missing_ok=True)

    def _append_and_read_record(self, payload: dict) -> tuple[dict, dict]:
        EVENT_LOG.unlink(missing_ok=True)
        with patch.object(
            unisender_go_events,
            "unisender_go_events_path",
            return_value=EVENT_LOG,
        ):
            result = unisender_go_events.append_unisender_go_events(payload)
        record = json.loads(EVENT_LOG.read_text(encoding="utf-8").splitlines()[0])
        return result, record

    def test_append_events_accepts_json_string_metadata(self) -> None:
        payload = {
            "event_type": "delivered",
            "email": "recipient@example.com",
            "metadata": json.dumps(
                {
                    "app_job_id": "job-webhook",
                    "app_row_id": "42",
                    "app_mun_name": "Test Municipality",
                }
            ),
        }

        result, record = self._append_and_read_record(payload)

        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(record["event_type"], "delivered")
        self.assertEqual(record["recipient"], "recipient@example.com")
        self.assertEqual(record["row_id"], "42")
        self.assertEqual(record["mun_name"], "Test Municipality")

    def test_append_events_accepts_nested_json_string_metadata(self) -> None:
        payload = {
            "event": {
                "status": "opened",
                "recipient": {"email": "recipient@example.com"},
                "message": {
                    "metadata": json.dumps(
                        {
                            "app_job_id": "job-webhook",
                            "row_id": "99",
                        }
                    )
                },
            }
        }

        result, record = self._append_and_read_record(payload)

        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(record["event_type"], "opened")
        self.assertEqual(record["row_id"], "99")

    def test_append_events_accepts_unisender_go_events_by_user_envelope(self) -> None:
        payload = {
            "events_by_user": [
                {
                    "project_id": 123,
                    "events": [
                        {
                            "event_name": "transactional_email_status",
                            "event_data": {
                                "job_id": "provider-job-1",
                                "email": "recipient@example.com",
                                "status": "delivered",
                                "event_time": "2026-06-02 10:15:00",
                                "metadata": {
                                    "app_job_id": "job-webhook",
                                    "app_row_id": "7",
                                    "app_mun_name": "Envelope Municipality",
                                },
                            },
                        }
                    ],
                }
            ]
        }

        result, record = self._append_and_read_record(payload)

        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(record["received_at"], "2026-06-02 10:15:00")
        self.assertEqual(record["event_type"], "delivered")
        self.assertEqual(record["recipient"], "recipient@example.com")
        self.assertEqual(record["provider_job_id"], "provider-job-1")
        self.assertEqual(record["row_id"], "7")


if __name__ == "__main__":
    unittest.main()
