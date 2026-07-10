from __future__ import annotations

import unittest
import unittest.mock

from src.generator.delivery.manager_stats import (
    TECHNICAL_TO_MANAGER_STATUS,
    StatsFilters,
    build_funnels,
    build_manager_dashboard,
    build_email_problems,
    interest_for,
    normalize_manager_status,
    recommended_action_for,
)
from src.generator.delivery import manager_stats


class ManagerStatusNormalizationTests(unittest.TestCase):
    def test_maps_delivered_statuses(self) -> None:
        for technical in ("delivered", "ok_delivered"):
            result = normalize_manager_status(technical)
            self.assertEqual(result["key"], "delivered")
            self.assertEqual(result["label"], "Доставлено")

    def test_maps_open_and_click(self) -> None:
        self.assertEqual(normalize_manager_status("opened")["key"], "opened")
        self.assertEqual(normalize_manager_status("ok_link_visited")["key"], "clicked")

    def test_maps_hard_and_soft_bounce(self) -> None:
        self.assertEqual(normalize_manager_status("hard_bounced")["key"], "email_broken")
        self.assertEqual(normalize_manager_status("soft_bounced")["key"], "soft_bounce")

    def test_maps_unknown_to_no_data(self) -> None:
        self.assertEqual(normalize_manager_status("unknown")["key"], "no_data")

    def test_all_specified_technical_statuses_have_mapping(self) -> None:
        expected = {
            "delivered",
            "opened",
            "clicked",
            "email_broken",
            "soft_bounce",
            "delivery_error",
            "unsubscribed",
            "spam",
            "pending",
            "no_data",
        }
        self.assertEqual(set(TECHNICAL_TO_MANAGER_STATUS.values()), expected)

    def test_interest_and_recommended_action(self) -> None:
        self.assertEqual(interest_for("clicked")["label"], "Высокий")
        self.assertEqual(recommended_action_for("email_broken")["label"], "Найти другой email")


class ManagerStatsAggregationTests(unittest.TestCase):
    def test_build_funnels(self) -> None:
        funnel = build_funnels(
            counts={
                "consents": 10,
                "sent": 20,
                "delivered": 18,
                "opened": 9,
                "clicked": 3,
            }
        )
        self.assertEqual(len(funnel), 5)
        self.assertEqual(funnel[0]["value"], 10)
        self.assertEqual(funnel[-1]["value"], 3)

    def test_dashboard_cards_from_delivery_rows(self) -> None:
        def _row(**kwargs: object) -> dict:
            provider_status = str(kwargs.pop("provider_status"))
            role = str(kwargs.pop("recipient_role"))
            return {
                **kwargs,
                "provider_status": provider_status,
                "recipient_role": role,
                "role": role,
                "manager_status": normalize_manager_status(provider_status),
            }

        delivery_rows = [
            _row(
                row_id="1",
                mun_name="ООО Тест",
                recipient="a@example.com",
                provider="rusender",
                provider_status="delivered",
                recipient_role="primary",
                sent_at="2026-05-01T10:00:00",
                sent_at_timestamp="2026-05-01T10:00:00",
            ),
            _row(
                row_id="2",
                mun_name="АО Пример",
                recipient="b@example.com",
                provider="mailopost",
                provider_status="opened",
                recipient_role="fallback",
                sent_at="2026-05-02T10:00:00",
                sent_at_timestamp="2026-05-02T10:00:00",
            ),
            _row(
                row_id="3",
                mun_name="ООО Ошибка",
                recipient="bad@example.com",
                provider="smtp",
                provider_status="hard_bounced",
                recipient_role="primary",
                sent_at="2026-05-03T10:00:00",
                sent_at_timestamp="2026-05-03T10:00:00",
                delivery_response="user unknown",
            ),
        ]
        consent_rows = [
            {
                "row_id": "1",
                "mun_name": "ООО Тест",
                "recipient": "a@example.com",
                "consent_status_key": "confirmed",
                "status_label": "Согласие получено",
                "materials_status": "sent",
                "materials_sent_at": "2026-05-01T12:00:00",
            }
        ]

        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=delivery_rows), unittest.mock.patch.object(
            manager_stats, "_load_consents_for_jobs", return_value=consent_rows
        ):
            result = build_manager_dashboard(StatsFilters(job_ids=("job-test",)))

        self.assertEqual(result["summary"]["sent"], 3)
        self.assertEqual(result["summary"]["delivered"], 2)
        self.assertEqual(result["summary"]["opened"], 1)
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertEqual(result["summary"]["consents"], 1)
        self.assertFalse(result["empty"])
        self.assertTrue(result["cards"])
        self.assertTrue(result["funnels"])
        self.assertIn("interested", result["work_lists"])


class EmailProblemsTests(unittest.TestCase):
    def test_email_problems_filters_problem_rows(self) -> None:
        rows = [
            {
                "row_key": "abc",
                "job_id": "job-test",
                "organization": "ООО Тест",
                "email": "bad@example.com",
                "provider": "rusender",
                "attempts": 2,
                "last_event_at": "2026-05-01",
                "manager_status": {"key": "email_broken", "label": "Email не работает", "tone": "bad", "category": "problem"},
                "bounce_reason": "email_not_exists",
                "bounce_reason_label": "Email не существует",
                "recommended_action": {"key": "find_another_email", "label": "Найти другой email"},
            }
        ]

        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), unittest.mock.patch.object(
            manager_stats, "_apply_recipient_filters", side_effect=lambda items, filters: items
        ):
            result = build_email_problems(StatsFilters(job_ids=("job-test",)))

        self.assertEqual(result["summary"]["problem_addresses"], 1)
        self.assertEqual(result["summary"]["hard_bounce"], 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["bounce_reason_label"], "Email не существует")


if __name__ == "__main__":
    unittest.main()
