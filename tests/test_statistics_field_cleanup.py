from __future__ import annotations

import unittest
import unittest.mock

from src.generator.delivery import manager_stats
from src.generator.delivery.manager_stats import (
    StatsFilters,
    _apply_recipient_filters,
    build_consents_view,
    build_reports_view,
)


class ConsentsViewCleanupTests(unittest.TestCase):
    def _consent_rows(self) -> list[dict]:
        return [
            {
                "organization": "ООО Тест",
                "contact": "Иван",
                "email": "a@example.com",
                "consent_status_key": "confirmed",
                "materials_status": "sent",
                "materials_sent_at": "2026-05-01T12:00:00",
                "interest": {"key": "high", "label": "Высокий", "tone": "good"},
            },
            {
                "organization": "АО Пример",
                "contact": "Пётр",
                "email": "b@example.com",
                "consent_status_key": "confirmed",
                "materials_status": "",
                "materials_sent_at": "",
                "interest": {"key": "medium", "label": "Средний", "tone": "warn"},
            },
            {
                "organization": "ЗАО Ждём",
                "contact": "Сидор",
                "email": "c@example.com",
                "consent_status_key": "pending",
                "materials_status": "",
                "materials_sent_at": "",
                "interest": {"key": "low", "label": "Низкий", "tone": "neutral"},
            },
        ]

    def test_summary_drops_clicked_after_consent(self) -> None:
        with unittest.mock.patch.object(manager_stats, "_load_company_consents_for_jobs", return_value=self._consent_rows()):
            result = build_consents_view(StatsFilters(job_ids=("job-test",)))

        summary = result["summary"]
        self.assertNotIn("clicked_after_consent", summary)
        self.assertEqual(summary["confirmed"], 2)
        self.assertEqual(summary["materials_sent"], 1)
        self.assertEqual(summary["opened_after_consent"], 1)
        self.assertEqual(summary["need_call"], 1)

    def test_funnel_is_consent_specific_three_steps(self) -> None:
        with unittest.mock.patch.object(manager_stats, "_load_company_consents_for_jobs", return_value=self._consent_rows()):
            result = build_consents_view(StatsFilters(job_ids=("job-test",)))

        funnel = result["funnel"]
        self.assertEqual([step["id"] for step in funnel], ["consent", "materials", "opened"])
        self.assertEqual(funnel[0]["value"], 2)
        self.assertEqual(funnel[1]["value"], 1)
        self.assertEqual(funnel[2]["value"], 1)
        # No misleading "Отправлено/Доставлено/Клик" steps carried over.
        labels = {step["label"] for step in funnel}
        self.assertNotIn("Клик", labels)


class ProviderFilterGroupingTests(unittest.TestCase):
    def test_unisender_filter_matches_go_and_classic_variants(self) -> None:
        rows = [
            {"provider": "unisender", "manager_status": {"key": "delivered"}},
            {"provider": "unisender_go", "manager_status": {"key": "opened"}},
            {"provider": "unisender_classic", "manager_status": {"key": "clicked"}},
            {"provider": "rusender", "manager_status": {"key": "delivered"}},
        ]
        filters = StatsFilters(job_ids=("job-test",), providers=("unisender",))
        result = _apply_recipient_filters(rows, filters)
        self.assertEqual(len(result), 3)
        self.assertEqual({row["provider"] for row in result}, {"unisender", "unisender_go", "unisender_classic"})


class ReportsViewCleanupTests(unittest.TestCase):
    def test_summary_drops_scheduled_and_keeps_csv(self) -> None:
        result = build_reports_view(())
        summary = result["summary"]
        self.assertNotIn("scheduled", summary)
        self.assertNotIn("scheduled", result)
        self.assertIn("csv", summary)
        self.assertEqual(summary["generated"], 0)


if __name__ == "__main__":
    unittest.main()
