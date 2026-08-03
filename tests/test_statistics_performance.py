from __future__ import annotations

import unittest
import unittest.mock

from src.generator.delivery import manager_stats
from src.generator.delivery.manager_stats import (
    StatsFilters,
    _job_awaiting_provider_events,
    build_campaigns,
    build_manager_dashboard,
    invalidate_stats_cache,
    warm_stats_cache,
)


def _delivery_row(job_id: str, row_id: str, status_key: str, **extra: object) -> dict:
    return {
        "job_id": job_id,
        "row_id": row_id,
        "recipient": f"{row_id}@example.com",
        "email": f"{row_id}@example.com",
        "recipient_name": f"{row_id}@example.com",
        "organization": "ООО Тест",
        "provider": "rusender",
        "role": "primary",
        "sent_at": "2026-05-01T10:00:00",
        "sent_at_timestamp": "2026-05-01T10:00:00",
        "manager_status": {"key": status_key, "label": status_key, "tone": "neutral", "category": "pending" if status_key in {"pending", "no_data"} else "success"},
        "next_action": {"key": "wait", "label": "Ожидать статус"},
        **extra,
    }


class AwaitingProviderEventsTests(unittest.TestCase):
    def test_empty_rows_not_awaiting(self) -> None:
        self.assertFalse(_job_awaiting_provider_events([]))

    def test_all_pending_is_awaiting(self) -> None:
        rows = [_delivery_row("job-a", "1", "pending"), _delivery_row("job-a", "2", "no_data")]
        self.assertTrue(_job_awaiting_provider_events(rows))

    def test_any_real_event_not_awaiting(self) -> None:
        rows = [_delivery_row("job-a", "1", "pending"), _delivery_row("job-a", "2", "delivered")]
        self.assertFalse(_job_awaiting_provider_events(rows))


class DashboardBackgroundRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        invalidate_stats_cache()

    def test_manual_refresh_starts_background_and_reports_flags(self) -> None:
        rows = [_delivery_row("job-a", "1", "pending")]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
                unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
                unittest.mock.patch.object(manager_stats, "_start_sender_delivery_refresh", return_value=True) as start, \
                unittest.mock.patch.object(manager_stats, "_is_sender_delivery_refresh_running", return_value=True):
            result = build_manager_dashboard(StatsFilters(job_ids=("job-a",)), refresh=True)

        start.assert_called_once_with("job-a")
        self.assertTrue(result["refresh_started"])
        self.assertTrue(result["refresh_in_progress"])

    def test_auto_refresh_when_only_pending(self) -> None:
        rows = [_delivery_row("job-a", "1", "pending")]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
                unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
                unittest.mock.patch.object(manager_stats, "_start_sender_delivery_refresh", return_value=True) as start, \
                unittest.mock.patch.object(manager_stats, "_is_sender_delivery_refresh_running", return_value=False):
            build_manager_dashboard(StatsFilters(job_ids=("job-a",)), refresh=False)

        start.assert_called_once_with("job-a")

    def test_no_background_when_data_present(self) -> None:
        rows = [_delivery_row("job-a", "1", "delivered")]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
                unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
                unittest.mock.patch.object(manager_stats, "_start_sender_delivery_refresh", return_value=True) as start, \
                unittest.mock.patch.object(manager_stats, "_is_sender_delivery_refresh_running", return_value=False):
            build_manager_dashboard(StatsFilters(job_ids=("job-a",)), refresh=False)

        start.assert_not_called()


class DeliveryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        invalidate_stats_cache()

    def test_cache_reuses_rows_within_ttl(self) -> None:
        calls: list[str] = []

        def _fake_build(job_id: str, *, refresh: bool) -> list[dict]:
            calls.append(job_id)
            return [_delivery_row(job_id, "1", "pending")]

        with unittest.mock.patch.object(manager_stats, "_build_delivery_rows_for_job", side_effect=_fake_build):
            manager_stats._load_delivery_for_jobs(("job-a",))
            manager_stats._load_delivery_for_jobs(("job-a",))

        self.assertEqual(calls, ["job-a"])  # built once, second read from cache

    def test_refresh_forces_rebuild(self) -> None:
        calls: list[str] = []

        def _fake_build(job_id: str, *, refresh: bool) -> list[dict]:
            calls.append(job_id)
            return [_delivery_row(job_id, "1", "pending")]

        with unittest.mock.patch.object(manager_stats, "_build_delivery_rows_for_job", side_effect=_fake_build):
            manager_stats._load_delivery_for_jobs(("job-a",))
            manager_stats._load_delivery_for_jobs(("job-a",), refresh=True)

        self.assertEqual(calls, ["job-a", "job-a"])  # refresh rebuilds


class BuildCampaignsSingleLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        invalidate_stats_cache()

    def test_delivery_loaded_once_for_all_jobs(self) -> None:
        rows = [
            _delivery_row("job-a", "1", "delivered"),
            _delivery_row("job-b", "1", "opened"),
            _delivery_row("job-c", "1", "pending"),
        ]
        load_calls: list[tuple] = []

        def _fake_load(job_ids, *, refresh=False):
            load_calls.append(tuple(job_ids))
            return [row for row in rows if row["job_id"] in set(job_ids)]

        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", side_effect=_fake_load), \
                unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
                unittest.mock.patch.object(manager_stats, "_load_campaign_statuses", return_value={}), \
                unittest.mock.patch.object(manager_stats, "_campaign_metadata", return_value={"title": "T", "work_type": "", "work_type_label": ""}), \
                unittest.mock.patch.object(manager_stats, "_campaign_status", return_value="draft"), \
                unittest.mock.patch.object(manager_stats, "_campaign_period", return_value=("2026-05-01", "2026-05-01")):
            result = build_campaigns(StatsFilters(job_ids=("job-a", "job-b", "job-c")))

        # A single bulk load for all jobs — not one call per job (no N+1).
        self.assertEqual(len(load_calls), 1)
        self.assertEqual(len(result["campaigns"]), 3)


class WarmStatsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        invalidate_stats_cache()

    def test_warm_loads_delivery_and_consent_for_all_jobs(self) -> None:
        job_ids = ("job-a", "job-b")
        delivery_calls: list[tuple] = []
        consent_calls: list[tuple] = []

        def _fake_delivery(ids, *, refresh=False):
            delivery_calls.append(tuple(ids))
            return [_delivery_row(job_id, "1", "delivered") for job_id in ids]

        def _fake_consents(ids):
            consent_calls.append(tuple(ids))
            return []

        with unittest.mock.patch.object(manager_stats, "list_job_ids_with_sent_mail", return_value=list(job_ids)), \
                unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", side_effect=_fake_delivery) as load_delivery, \
                unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", side_effect=_fake_consents) as load_consents, \
                unittest.mock.patch.object(manager_stats, "_trigger_provider_refresh") as trigger_refresh:
            result = warm_stats_cache()

        self.assertEqual(result["jobs"], 2)
        load_delivery.assert_called_once_with(job_ids)
        load_consents.assert_called_once_with(job_ids)
        trigger_refresh.assert_not_called()

    def test_warm_handles_no_jobs(self) -> None:
        with unittest.mock.patch.object(manager_stats, "list_job_ids_with_sent_mail", return_value=[]), \
                unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs") as load_delivery, \
                unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs") as load_consents:
            result = warm_stats_cache()

        self.assertEqual(result["jobs"], 0)
        load_delivery.assert_not_called()
        load_consents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
