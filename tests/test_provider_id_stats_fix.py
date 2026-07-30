from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.generator.delivery import repair_unmatched_events, sender_report
from src.generator.delivery.provider_ids import (
    normalize_provider_message_id,
    provider_message_id_lookup_keys,
)


class ProviderIdNormalizeTests(unittest.TestCase):
    def test_strips_known_prefixes(self) -> None:
        self.assertEqual(normalize_provider_message_id("rusender:abc-1"), "abc-1")
        self.assertEqual(normalize_provider_message_id("mailopost:xyz"), "xyz")
        self.assertEqual(normalize_provider_message_id("unisender_go:job-9"), "job-9")
        self.assertEqual(normalize_provider_message_id("abc-1"), "abc-1")
        self.assertEqual(normalize_provider_message_id(""), "")

    def test_lookup_keys_include_bare_and_original(self) -> None:
        self.assertEqual(provider_message_id_lookup_keys("rusender:abc"), ["abc", "rusender:abc"])
        self.assertEqual(provider_message_id_lookup_keys("abc"), ["abc"])


class PrefixedIdMatchingTests(unittest.TestCase):
    def test_match_rusender_prefixed_log_to_bare_event(self) -> None:
        events = {
            "task:bare-task": {"provider_status": "delivered", "task_id": "bare-task"},
            "task_email:bare-task:a@example.com": {
                "provider_status": "opened",
                "task_id": "bare-task",
                "recipient": "a@example.com",
            },
        }
        item = {
            "transport": "rusender",
            "provider_message_id": "rusender:bare-task",
            "recipient": "a@example.com",
        }
        matched = sender_report._match_rusender_event(item, events)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["provider_status"], "opened")

    def test_match_mailopost_prefixed_log_to_bare_event(self) -> None:
        events = {
            "message:mid-1": {"provider_status": "delivered", "message_id": "mid-1"},
        }
        item = {
            "transport": "mailopost",
            "provider_message_id": "mailopost:mid-1",
            "recipient": "b@example.com",
        }
        matched = sender_report._match_mailopost_event(item, events)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["provider_status"], "delivered")

    def test_message_id_helper_strips_prefix(self) -> None:
        mid = sender_report._message_id({"provider_message_id": "rusender:task-9"})
        self.assertEqual(mid, "task-9")


class SmtpDeliveryRowsTests(unittest.TestCase):
    def test_smtp_sent_becomes_delivered_in_build_rows(self) -> None:
        items = [
            {
                "transport": "smtp",
                "status": "sent",
                "row_id": "1",
                "recipient": "a@example.com",
                "sent_at": "2026-01-01T10:00:00+00:00",
            }
        ]
        with (
            patch.object(sender_report, "_load_delivery_log_items", return_value=items),
            patch.object(sender_report, "_current_data_recipient_roles", return_value={}),
            patch.object(sender_report, "_latest_unisender_go_events", return_value={}),
            patch.object(sender_report, "_latest_rusender_events", return_value={}),
            patch.object(sender_report, "_latest_mailopost_events", return_value={}),
            patch.object(sender_report, "_load_delivery_status_cache", return_value={}),
        ):
            rows, _ = sender_report._build_delivery_rows("job-smtp", refresh=False, for_statistics=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider_status"], "delivered")


class RepairUnmatchedTests(unittest.TestCase):
    def test_repair_moves_rusender_unmatched_by_bare_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unmatched = root / "rusender_events_unmatched.jsonl"
            record = {
                "event_key": "ek-1",
                "task_id": "bare-1",
                "provider_status": "delivered",
                "recipient": "a@example.com",
            }
            unmatched.write_text(json.dumps(record) + "\n", encoding="utf-8")
            appended: list[dict[str, Any]] = []

            def fake_unmatched() -> Path:
                return unmatched

            def fake_path(job_id: str | None) -> Path:
                return root / "jobs" / str(job_id) / "state" / "rusender_events.jsonl"

            index = {"bare-1": {"job_id": "job-1", "row_id": "10", "recipient": "a@example.com"}}
            with (
                patch.object(repair_unmatched_events, "_rusender_unmatched_path", fake_unmatched),
                patch.object(repair_unmatched_events, "rusender_events_path", fake_path),
                patch.object(repair_unmatched_events, "_load_task_job_index", return_value=index),
                patch.object(repair_unmatched_events, "_event_keys", return_value=set()),
                patch.object(
                    repair_unmatched_events,
                    "append_jsonl",
                    side_effect=lambda path, rec: appended.append(rec),
                ),
                patch("src.generator.delivery.manager_stats.invalidate_stats_cache"),
            ):
                report = repair_unmatched_events.repair_unmatched_rusender_events(dry_run=False)

            self.assertEqual(report["moved"], 1)
            self.assertEqual(report["remaining"], 0)
            self.assertEqual(appended[0]["task_id"], "bare-1")
            self.assertEqual(unmatched.read_text(encoding="utf-8").strip(), "")


class LayoutErrorCompanyAggregationTests(unittest.TestCase):
    def test_company_row_keeps_layout_error_code(self) -> None:
        from src.generator.delivery.manager_stats import _build_company_row

        group = [
            {
                "job_id": "j1",
                "row_id": "42",
                "email": "a@example.com",
                "role": "primary",
                "role_label": "Основной",
                "manager_status": {"key": "delivery_error", "label": "Ошибка", "tone": "bad", "category": "problem"},
                "organization": "ООО Тест",
                "layout_error_code": "kp_font_compact",
                "provider": "smtp",
            }
        ]
        row = _build_company_row(group)
        self.assertEqual(row["layout_error_code"], "kp_font_compact")


if __name__ == "__main__":
    unittest.main()
