from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.generator.delivery import rusender_events


EVENT_LOG = Path(__file__).with_name("_rusender_events_test.jsonl")


class RuSenderEventsTests(unittest.TestCase):
    def tearDown(self) -> None:
        EVENT_LOG.unlink(missing_ok=True)

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
        ), patch.object(
            rusender_events,
            "rusender_events_path",
            return_value=EVENT_LOG,
        ):
            result = rusender_events.append_rusender_events(payload)

        record = json.loads(EVENT_LOG.read_text(encoding="utf-8").splitlines()[0])
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
        ), patch.object(
            rusender_events,
            "rusender_events_path",
            return_value=EVENT_LOG,
        ):
            result = rusender_events.append_rusender_events(payload)

        record = json.loads(EVENT_LOG.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(result["saved"], 1)
        self.assertEqual(record["provider_status"], "clicked")
        self.assertEqual(record["task_id"], "mailing-agent:job-webhook:42:recipient@example.com:abc")


    def test_append_events_skips_duplicate_payload(self) -> None:
        EVENT_LOG.unlink(missing_ok=True)
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
        ), patch.object(
            rusender_events,
            "rusender_events_path",
            return_value=EVENT_LOG,
        ):
            first = rusender_events.append_rusender_events(payload)
            second = rusender_events.append_rusender_events(payload)

        lines = EVENT_LOG.read_text(encoding="utf-8").splitlines()
        self.assertEqual(first["saved"], 1)
        self.assertEqual(first["duplicates"], 0)
        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(lines), 1)

if __name__ == "__main__":
    unittest.main()
