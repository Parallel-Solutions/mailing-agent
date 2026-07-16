from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generator.delivery import sender_report


class SmtpDeliveryStatusTests(unittest.TestCase):
    def test_smtp_sent_maps_to_delivered(self) -> None:
        status = sender_report._initial_log_provider_status(
            {"transport": "smtp", "status": "sent", "recipient": "a@example.com"}
        )
        self.assertEqual(status, "delivered")

    def test_smtp_failed_maps_to_failed(self) -> None:
        status = sender_report._initial_log_provider_status(
            {"transport": "smtp", "status": "failed", "recipient": "a@example.com"}
        )
        self.assertEqual(status, "failed")

    def test_rusender_keeps_accepted_until_webhook(self) -> None:
        status = sender_report._initial_log_provider_status(
            {
                "transport": "rusender",
                "status": "sent",
                "provider": {"provider": "rusender", "status": "accepted"},
            }
        )
        self.assertEqual(status, "accepted")

    def test_statistics_loader_skips_send_run_scope(self) -> None:
        items = [
            {"transport": "rusender", "send_run_id": "old", "row_id": "1", "recipient": "a@example.com"},
            {"transport": "rusender", "send_run_id": "new", "row_id": "2", "recipient": "b@example.com"},
        ]
        with (
            patch.object(sender_report, "_load_sent_mail_log_items", return_value=items),
            patch.object(sender_report, "_filter_items_by_current_sender_state") as scoped_state,
            patch.object(sender_report, "_filter_items_by_current_data") as scoped_data,
            patch.object(sender_report, "_filter_items_by_send_scope") as scoped_run,
        ):
            loaded = sender_report._load_delivery_log_items("job-x", for_statistics=True)

        self.assertEqual([item["row_id"] for item in loaded], ["1", "2"])
        scoped_state.assert_not_called()
        scoped_data.assert_not_called()
        scoped_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
