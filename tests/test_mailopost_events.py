from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from src.generator.delivery import mailopost_events
from src.generator.delivery.mailopost_events import load_mailopost_events
from tests.bootstrap import reset_test_database


class MailoPostEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_append_events_maps_message_id_to_job_and_status(self) -> None:
        payload = {
            "event": "opened",
            "message_id": "message-123",
            "email": "recipient@example.com",
            "occurred_at": "2026-06-18T12:10:00",
        }

        with patch.object(
            mailopost_events,
            "_load_message_job_index",
            return_value={"message-123": {"job_id": "job-webhook", "row_id": "42", "recipient": "recipient@example.com"}},
        ):
            result = mailopost_events.append_mailopost_events(payload)

        record = load_mailopost_events("job-webhook")[0]
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["unmatched"], 0)
        self.assertEqual(result["jobs"], ["job-webhook"])
        self.assertEqual(record["provider_status"], "opened")
        self.assertEqual(record["message_id"], "message-123")
        self.assertEqual(record["recipient"], "recipient@example.com")

    def test_append_events_skips_duplicate_payload(self) -> None:
        payload = {
            "event_id": "event-1",
            "event": "opened",
            "message_id": "message-123",
            "email": "recipient@example.com",
        }

        with patch.object(
            mailopost_events,
            "_load_message_job_index",
            return_value={"message-123": {"job_id": "job-webhook", "row_id": "42", "recipient": "recipient@example.com"}},
        ):
            first = mailopost_events.append_mailopost_events(payload)
            second = mailopost_events.append_mailopost_events(payload)

        records = load_mailopost_events("job-webhook")
        self.assertEqual(first["saved"], 1)
        self.assertEqual(first["duplicates"], 0)
        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(records), 1)

    def test_bounce_records_connection_guard_and_smtp_response(self) -> None:
        payload = {
            "event_id": "event-guard-1",
            "event": "hard_bounced",
            "message_id": "message-guard-1",
            "email": "missing@example.com",
            "bounce_reason": "550 5.1.1 user unknown",
        }
        message_index = {
            "message-guard-1": {
                "job_id": "job-webhook",
                "row_id": "77",
                "recipient": "missing@example.com",
                "connection_id": "connection-guard-1",
            }
        }
        with (
            patch.object(mailopost_events, "_load_message_job_index", return_value=message_index),
            patch("src.generator.delivery.suppression_store.upsert_from_provider_event") as suppress,
            patch("src.generator.delivery.channel_guard.record_channel_outcome") as record_outcome,
        ):
            result = mailopost_events.append_mailopost_events(payload)

        self.assertEqual(result["saved"], 1)
        record = load_mailopost_events("job-webhook")[-1]
        self.assertEqual(record["connection_id"], "connection-guard-1")
        self.assertEqual(record["smtp_response"], "550 5.1.1 user unknown")
        suppress.assert_called_once_with(
            recipient="missing@example.com",
            provider_status="hard_bounced",
            source="webhook_mailopost",
            job_id="job-webhook",
            delivery_response="550 5.1.1 user unknown",
        )
        record_outcome.assert_called_once_with(
            connection_id="connection-guard-1",
            provider_message_id="message-guard-1",
            provider_status="hard_bounced",
            recipient="missing@example.com",
            smtp_response="550 5.1.1 user unknown",
            occurred_at="",
        )

    def test_unmatched_event_is_forwarded_to_sender_warmup(self) -> None:
        message_id = f"warmup-{uuid4()}"
        payload = {
            "event_id": f"event-{uuid4()}",
            "event": "delivered",
            "message_id": message_id,
            "email": "friend@gmail.com",
        }
        with (
            patch.object(mailopost_events, "_load_message_job_index", return_value={}),
            patch("src.campaigns.connection_sender_warmup_service.record_warmup_delivery_outcome") as record_warmup,
        ):
            result = mailopost_events.append_mailopost_events(payload)

        self.assertEqual(result["unmatched"], 1)
        record_warmup.assert_called_once_with(
            provider_message_id=message_id,
            provider_status="delivered",
            smtp_response="",
        )

    def test_send_time_lookup_resolves_job_without_full_sent_mail_log_scan(self) -> None:
        from src.jobs.provider_events_store import upsert_provider_task_lookup

        upsert_provider_task_lookup(
            provider_task_id="message-fast-1",
            job_id="job-fast",
            campaign_id=None,
            connection_id=None,
            recipient="fast@example.com",
            row_id="9",
        )
        payload = {"event": "delivered", "message_id": "message-fast-1", "email": "fast@example.com"}

        def _boom() -> dict[str, dict[str, str]]:
            raise AssertionError("full sent_mail_log scan should not run when the fast lookup covers all ids")

        with patch.object(mailopost_events, "_load_message_job_index", side_effect=_boom):
            result = mailopost_events.append_mailopost_events(payload)

        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["jobs"], ["job-fast"])
        record = load_mailopost_events("job-fast")[0]
        self.assertEqual(record["recipient"], "fast@example.com")


if __name__ == "__main__":
    unittest.main()
