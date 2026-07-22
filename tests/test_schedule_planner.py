from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.campaigns.schedule_planner import plan_batches


class SchedulePlannerTests(unittest.TestCase):
    def test_empty_recipients(self) -> None:
        plan = plan_batches(recipient_count=0, batch_size=10, interval_seconds=60, start_at=None)
        self.assertEqual(plan["batch_count"], 0)

    def test_batches_split_and_interval(self) -> None:
        start = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)
        plan = plan_batches(
            recipient_count=55,
            batch_size=25,
            interval_seconds=300,
            start_at=start,
            send_immediately=False,
            timezone_name="UTC",
            weekdays=[0, 1, 2, 3, 4, 5, 6],
            time_windows=[{"start": "00:00", "end": "23:59"}],
            now=start,
        )
        self.assertEqual(plan["batch_count"], 3)
        self.assertEqual(plan["batches"][0]["size"], 25)
        self.assertEqual(plan["batches"][2]["size"], 5)

    def test_max_per_hour_caps_batch(self) -> None:
        start = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        plan = plan_batches(
            recipient_count=20,
            batch_size=25,
            interval_seconds=60,
            start_at=start,
            send_immediately=False,
            timezone_name="UTC",
            weekdays=list(range(7)),
            max_per_hour=5,
            now=start,
        )
        self.assertTrue(all(b["size"] <= 5 for b in plan["batches"]))
        self.assertEqual(sum(b["size"] for b in plan["batches"]), 20)

    def test_past_start_at_clamped_to_now(self) -> None:
        now = datetime(2026, 7, 22, 16, 13, tzinfo=timezone.utc)
        past_start = datetime(2024, 7, 22, 16, 8, tzinfo=timezone.utc)
        plan = plan_batches(
            recipient_count=1,
            batch_size=25,
            interval_seconds=3600,
            start_at=past_start,
            send_immediately=False,
            timezone_name="Europe/Moscow",
            weekdays=[0, 1, 2, 3, 4],
            time_windows=[{"start": "09:00", "end": "18:00"}],
            now=now,
        )
        first_at = datetime.fromisoformat(str(plan["first_batch_at"]).replace("Z", "+00:00"))
        self.assertGreaterEqual(first_at, now)
        self.assertFalse(str(plan["first_batch_at"]).startswith("2024"))


if __name__ == "__main__":
    unittest.main()
