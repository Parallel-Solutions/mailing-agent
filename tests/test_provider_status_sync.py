from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generator.delivery import provider_status_sync


class ProviderStatusSyncTests(unittest.TestCase):
    def test_collects_message_ids_by_transport(self) -> None:
        items = [
            {
                "transport": "rusender",
                "provider_message_id": "task-1",
                "provider": {"provider": "rusender", "uuid": "task-1"},
            },
            {
                "transport": "smtp",
                "provider_message_id": "smtp-1",
            },
            {
                "transport": "mailopost",
                "provider": {"provider": "mailopost", "message_id": "mp-1"},
            },
        ]
        with (
            patch.object(provider_status_sync, "list_job_ids_with_sent_mail", return_value=["job-a"]),
            patch.object(provider_status_sync, "read_sent_mail_log", return_value=items),
        ):
            buckets = provider_status_sync.collect_provider_message_ids()

        self.assertEqual(buckets["rusender"], [("job-a", "task-1")])
        self.assertEqual(buckets["mailopost"], [("job-a", "mp-1")])

    def test_rusender_api_payload_maps_status_to_webhook_event(self) -> None:
        events = provider_status_sync._rusender_payload_from_api(
            "task-9",
            {"status": "delivered", "email": "a@example.com"},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trigger"], "external_mail.delivered")
        self.assertEqual(events[0]["payload"]["taskId"], "task-9")

    def test_sync_reports_empty_when_no_provider_ids(self) -> None:
        with patch.object(provider_status_sync, "collect_provider_message_ids", return_value={"rusender": [], "mailopost": []}):
            report = provider_status_sync.sync_provider_delivery_events()
        self.assertEqual(report["counts"]["rusender"], 0)
        self.assertEqual(report["providers"]["rusender"]["requested"], 0)


if __name__ == "__main__":
    unittest.main()
