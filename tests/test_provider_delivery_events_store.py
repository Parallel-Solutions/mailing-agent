from __future__ import annotations

import unittest

from src.jobs.provider_events_store import (
    append_provider_event,
    has_provider_events,
    load_provider_events,
)
from tests.bootstrap import reset_test_database


class ProviderDeliveryEventsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_append_and_load_round_trips_payload(self) -> None:
        inserted = append_provider_event(
            source="rusender",
            job_id="job-1",
            provider_task_id="task-1",
            recipient="user@example.com",
            event_type="external_mail.delivered",
            provider_status="delivered",
            occurred_at="2026-06-18T12:10:00",
            event_key="key-1",
            payload={"provider_status": "delivered", "recipient": "user@example.com"},
        )

        self.assertTrue(inserted)
        self.assertTrue(has_provider_events("rusender", "job-1"))
        events = load_provider_events("rusender", "job-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["provider_status"], "delivered")
        self.assertEqual(events[0]["recipient"], "user@example.com")

    def test_duplicate_event_key_is_not_inserted_twice(self) -> None:
        first = append_provider_event(
            source="mailopost",
            job_id="job-2",
            provider_task_id="message-1",
            event_key="dup-key",
            payload={"n": 1},
        )
        second = append_provider_event(
            source="mailopost",
            job_id="job-2",
            provider_task_id="message-1",
            event_key="dup-key",
            payload={"n": 2},
        )

        self.assertTrue(first)
        self.assertFalse(second)
        events = load_provider_events("mailopost", "job-2")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["n"], 1)

    def test_same_event_key_different_source_is_independent(self) -> None:
        append_provider_event(source="rusender", job_id="job-3", event_key="shared-key", payload={"src": "rusender"})
        append_provider_event(source="mailopost", job_id="job-3", event_key="shared-key", payload={"src": "mailopost"})

        self.assertEqual(len(load_provider_events("rusender", "job-3")), 1)
        self.assertEqual(len(load_provider_events("mailopost", "job-3")), 1)

    def test_empty_event_key_is_never_inserted(self) -> None:
        inserted = append_provider_event(source="rusender", job_id="job-4", event_key="", payload={})
        self.assertFalse(inserted)
        self.assertFalse(has_provider_events("rusender", "job-4"))

    def test_load_and_has_events_require_job_id(self) -> None:
        self.assertEqual(load_provider_events("rusender", None), [])
        self.assertEqual(load_provider_events("rusender", ""), [])
        self.assertFalse(has_provider_events("rusender", None))


if __name__ == "__main__":
    unittest.main()
