from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generator.delivery import rusender_events
from src.generator.delivery.rusender_events import load_rusender_events
from tests.bootstrap import reset_test_database


class RuSenderEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_append_events_maps_task_id_to_job_and_status(self) -> None:
        payload = {
            "trigger": "external_mail.open",
            "payload": {
                "taskId": "task-123",
                "email": "recipient@example.com",
            },
            "occurredAt": "2026-06-18T12:10:00",
        }

        with patch.object(
            rusender_events,
            "_load_task_job_index",
            return_value={"task-123": {"job_id": "job-webhook", "row_id": "42", "recipient": "recipient@example.com"}},
        ):
            result = rusender_events.append_rusender_events(payload)

        record = load_rusender_events("job-webhook")[0]
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["unmatched"], 0)
        self.assertEqual(result["jobs"], ["job-webhook"])
        self.assertEqual(record["provider_status"], "opened")
        self.assertEqual(record["task_id"], "task-123")
        self.assertEqual(record["recipient"], "recipient@example.com")

    def test_append_events_accepts_idempotency_key_as_task_id(self) -> None:
        payload = {
            "trigger": "external_mail.click",
            "payload": {
                "idempotencyKey": "mailing-agent:job-webhook:42:recipient@example.com:abc",
                "email": "recipient@example.com",
            },
        }

        with patch.object(
            rusender_events,
            "_load_task_job_index",
            return_value={
                "mailing-agent:job-webhook:42:recipient@example.com:abc": {
                    "job_id": "job-webhook",
                    "row_id": "42",
                    "recipient": "recipient@example.com",
                }
            },
        ):
            result = rusender_events.append_rusender_events(payload)

        record = load_rusender_events("job-webhook")[0]
        self.assertEqual(result["saved"], 1)
        self.assertEqual(record["provider_status"], "clicked")
        self.assertEqual(record["task_id"], "mailing-agent:job-webhook:42:recipient@example.com:abc")

    def test_append_events_skips_duplicate_payload(self) -> None:
        payload = {
            "eventId": "event-1",
            "trigger": "external_mail.open",
            "payload": {
                "taskId": "task-123",
                "email": "recipient@example.com",
            },
        }

        with patch.object(
            rusender_events,
            "_load_task_job_index",
            return_value={"task-123": {"job_id": "job-webhook", "row_id": "42", "recipient": "recipient@example.com"}},
        ):
            first = rusender_events.append_rusender_events(payload)
            second = rusender_events.append_rusender_events(payload)

        records = load_rusender_events("job-webhook")
        self.assertEqual(first["saved"], 1)
        self.assertEqual(first["duplicates"], 0)
        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
