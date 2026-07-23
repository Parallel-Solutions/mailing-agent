"""Integration tests: CampaignFlow sent_mail_log must appear in manager statistics."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.generator.delivery import manager_stats, sender_report
from src.generator.delivery.manager_stats import StatsFilters, build_manager_dashboard, invalidate_stats_cache
from src.jobs.job_docs import append_event
from tests.bootstrap import bootstrap_test_runtime


class CampaignStatisticsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        invalidate_stats_cache()

    def test_campaign_flow_sent_mail_visible_despite_legacy_filters(self) -> None:
        """CampaignFlow logs lack send_run_id and may disagree with stale data.xlsx."""
        job_id = "job-campaign-stats"
        recipient_id = 501
        email = "company@example.com"

        append_event(
            job_id,
            "sent_mail_log",
            {
                "row_id": str(recipient_id),
                "recipient": email,
                "transport": "rusender",
                "status": "sent",
                "campaign_id": "camp-test",
                "campaign_name": "Regions O and P",
                "sent_at": "2026-07-23T10:00:00+03:00",
                "provider_message_id": "msg-1",
            },
        )
        # Legacy sender state + mismatched data.xlsx would hide this row when
        # for_statistics=False (send_run scope and row_id=999 vs log row_id=501).
        sender_state = {
            "send_run_id": "send-legacy",
            "send_run_started_at": "2026-06-01T10:00:00",
            "total_rows": 1,
            "rows": [{"id": "1", "sent_recipients": ["legacy@example.com"]}],
        }
        data_path = Path(__file__)
        data_rows = [{"ID": "999", "EMAIL_OSN": "other@example.com"}]

        with (
            patch.object(sender_report, "_load_sender_state", return_value=sender_state),
            patch.object(sender_report, "_report_data_xlsx_path", return_value=data_path),
            patch.object(sender_report, "load_rows", return_value=(None, None, data_rows)),
            patch.object(manager_stats, "_start_sender_delivery_refresh", return_value=False),
            patch.object(manager_stats, "_is_sender_delivery_refresh_running", return_value=False),
        ):
            result = build_manager_dashboard(StatsFilters(job_ids=(job_id,)))

        self.assertGreaterEqual(result["summary"]["sent"], 1)
        self.assertFalse(result["empty"])


if __name__ == "__main__":
    unittest.main()
