from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

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

    def test_bounce_records_connection_guard_and_smtp_response(self) -> None:
        payload = {
            "eventId": "event-guard-1",
            "trigger": "external_mail.hard_bounced",
            "payload": {
                "taskId": "task-guard-1",
                "email": "missing@example.com",
                "smtpServerResponse": "550 5.1.1 user unknown",
            },
        }
        task_index = {
            "task-guard-1": {
                "job_id": "job-webhook",
                "row_id": "77",
                "recipient": "missing@example.com",
                "connection_id": "connection-guard-1",
            }
        }
        with (
            patch.object(rusender_events, "_load_task_job_index", return_value=task_index),
            patch("src.generator.delivery.suppression_store.upsert_from_provider_event") as suppress,
            patch("src.generator.delivery.channel_guard.record_channel_outcome") as record_outcome,
        ):
            result = rusender_events.append_rusender_events(payload)

        self.assertEqual(result["saved"], 1)
        record = load_rusender_events("job-webhook")[-1]
        self.assertEqual(record["connection_id"], "connection-guard-1")
        self.assertEqual(record["smtp_response"], "550 5.1.1 user unknown")
        suppress.assert_called_once_with(
            recipient="missing@example.com",
            provider_status="hard_bounced",
            source="webhook_rusender",
            job_id="job-webhook",
            delivery_response="550 5.1.1 user unknown",
        )
        record_outcome.assert_called_once_with(
            connection_id="connection-guard-1",
            provider_message_id="task-guard-1",
            provider_status="hard_bounced",
            recipient="missing@example.com",
            smtp_response="550 5.1.1 user unknown",
            occurred_at="",
        )


    def test_unmatched_event_is_forwarded_to_sender_warmup(self) -> None:
        task_id = f"warmup-{uuid4()}"
        payload = {
            "eventId": f"event-{uuid4()}",
            "trigger": "external_mail.delivered",
            "payload": {"taskId": task_id, "email": "friend@gmail.com"},
        }
        with (
            patch.object(rusender_events, "_load_task_job_index", return_value={}),
            patch("src.campaigns.connection_sender_warmup_service.record_warmup_delivery_outcome") as record_warmup,
        ):
            result = rusender_events.append_rusender_events(payload)

        self.assertEqual(result["unmatched"], 1)
        record_warmup.assert_called_once_with(
            provider_message_id=task_id,
            provider_status="delivered",
            smtp_response="",
        )

if __name__ == "__main__":
    unittest.main()
