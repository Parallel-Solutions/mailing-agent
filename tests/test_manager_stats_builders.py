"""Unit tests for the statistics builder functions in manager_stats.

These cover the pure helpers and every ``build_*`` view function (with the
delivery/consent loaders mocked), asserting structure, pagination, rates and the
production wording (no anglicisms/slang such as "Клик"/"Open Rate").
"""
from __future__ import annotations

import unittest
import unittest.mock

from src.generator.delivery import manager_stats
from src.generator.delivery.manager_stats import (
    COMPANY_DATA_PLACEHOLDER,
    StatsFilters,
    _aggregate_counts,
    _bounce_reason,
    _company_view,
    _group_rows_into_companies,
    _pct,
    _within_period,
    build_campaign_analytics,
    build_campaign_attempts,
    build_campaign_full_analytics,
    build_campaigns,
    build_consents_view,
    build_email_problems,
    build_funnels,
    build_insights,
    build_manager_dashboard,
    build_recipient_detail,
    build_recipients,
    build_reports_view,
    build_work_lists,
    interest_for,
    list_available_reports,
    normalize_statistics_period,
    make_row_key,
    normalize_manager_status,
    parse_row_key,
    recommended_action_for,
)


def _delivery_row(job_id, row_id, org, email, provider, provider_status, role="primary", delivery_response=""):
    """Build a fully-formed delivery row mirroring _build_delivery_rows_for_job."""
    status = normalize_manager_status(provider_status)
    return {
        "row_id": row_id,
        "mun_name": org,
        "recipient": email,
        "recipient_role": role,
        "provider": provider,
        "provider_status": provider_status,
        "delivery_response": delivery_response,
        "sent_at": "2026-05-01T10:00:00",
        "checked_at": "2026-05-02T10:00:00",
        "job_id": job_id,
        "row_key": make_row_key(job_id, row_id, email),
        "organization": org,
        "recipient_name": email,
        "email": email.lower(),
        "role": role,
        "role_label": "Основной адрес",
        "manager_status": status,
        "interest": interest_for(status["key"]),
        "recommended_action": recommended_action_for(status["key"]),
        "next_action": recommended_action_for(status["key"]),
        "last_event_at": "2026-05-02T10:00:00",
        "last_event_label": status["label"],
        "attempts": 1,
        "bounce_reason": _bounce_reason(provider_status, delivery_response),
        "bounce_reason_label": "Прочее",
        "email_domain_provider": "mail.ru",
    }


def _consent_row(job_id, row_id, org, email, status_key="confirmed", materials="sent"):
    """Mirror _build_consent_rows_for_job output (fields consumed by the UI)."""
    sent = materials == "sent"
    return {
        "job_id": job_id,
        "row_id": row_id,
        "organization": org,
        "contact": f"Контакт {row_id}",
        "email": email,
        "consent_status_key": status_key,
        "consent_status_label": "Согласие получено" if status_key == "confirmed" else "Ожидает",
        "materials_status": materials,
        "materials_label": "Материалы отправлены" if sent else "Материалы ещё не отправлялись",
        "materials_sent_at": "2026-05-03T09:00:00" if sent else "",
        "last_action_label": "Материалы отправлены" if sent else "Ожидает",
        "last_action_at": "2026-05-03T09:00:00",
        "interest": interest_for("clicked" if status_key == "confirmed" else "pending"),
        "next_action": recommended_action_for("opened" if status_key == "confirmed" else "pending"),
    }


class PureHelperTests(unittest.TestCase):
    def test_status_labels_are_plain_russian(self) -> None:
        self.assertEqual(normalize_manager_status("clicked")["label"], "Переход по ссылке")
        self.assertEqual(normalize_manager_status("pending")["label"], "Ожидают статуса")
        self.assertEqual(normalize_manager_status("delivered")["label"], "Доставлено")

    def test_interest_and_recommended(self) -> None:
        self.assertEqual(interest_for("clicked")["label"], "Высокий")
        self.assertEqual(recommended_action_for("email_broken")["label"], "Найти другой email")

    def test_row_key_round_trip(self) -> None:
        key = make_row_key("job-1", "42", "User@Example.com")
        job_id, row_id, email = parse_row_key(key)
        self.assertEqual(job_id, "job-1")
        self.assertEqual(row_id, "42")
        self.assertEqual(email, "user@example.com")

    def test_pct(self) -> None:
        self.assertEqual(_pct(1, 4), 25.0)
        self.assertEqual(_pct(0, 0), 0.0)

    def test_bounce_reason(self) -> None:
        self.assertTrue(_bounce_reason("hard_bounced", "user unknown"))

    def test_period_filter_handles_timezone_aware_and_legacy_values(self) -> None:
        bounds = {"period_from": "2026-05-03", "period_to": "2026-05-03"}
        self.assertTrue(_within_period("2026-05-03T23:59:59.999999+03:00", **bounds))
        self.assertTrue(_within_period("2026-05-03T20:59:59.999999Z", **bounds))
        self.assertTrue(_within_period("2026-05-03T23:59:59.999999", **bounds))
        self.assertFalse(_within_period("2026-05-03T21:00:00Z", **bounds))
        self.assertFalse(_within_period("2026-05-04T00:00:00+03:00", **bounds))

    def test_period_validation_rejects_invalid_or_reversed_dates(self) -> None:
        with self.assertRaises(ValueError):
            normalize_statistics_period("03.05.2026", "2026-05-03")
        with self.assertRaises(ValueError):
            normalize_statistics_period("2026-05-04", "2026-05-03")


class FunnelTests(unittest.TestCase):
    def test_labels_and_percentages(self) -> None:
        funnel = build_funnels(counts={"consents": 10, "sent": 20, "delivered": 18, "opened": 9, "clicked": 3})
        labels = [step["label"] for step in funnel]
        self.assertEqual(
            labels,
            ["Согласие", "Принято провайдером", "Доставлено", "Открыто", "Переходы"],
        )
        self.assertNotIn("Клик", labels)
        # Percent is computed against sent (companies in the mailing).
        self.assertEqual(funnel[0]["percent"], 50.0)
        self.assertEqual(funnel[1]["percent"], 100.0)
        self.assertEqual(funnel[-1]["value"], 3)

    def test_campaign_funnel_uses_all_attempts_as_its_base(self) -> None:
        funnel = build_funnels(
            counts={
                "total_attempts": 25,
                "consents": 2,
                "sent": 20,
                "delivered": 18,
                "opened": 9,
                "clicked": 3,
            }
        )
        by_id = {step["id"]: step for step in funnel}

        self.assertEqual(by_id["sent"]["percent"], 80.0)
        self.assertEqual(by_id["delivered"]["percent"], 72.0)
        self.assertEqual(by_id["opened"]["percent"], 36.0)
        self.assertEqual(by_id["sent"]["base"], 25)
        self.assertEqual(by_id["sent"]["base_label"], "всех попыток отправки")

    def test_funnel_percentages_never_exceed_100_for_mailing_steps(self) -> None:
        funnel = build_funnels(
            counts={"consents": 10, "sent": 1000, "delivered": 850, "opened": 300, "clicked": 40},
        )
        by_id = {step["id"]: step for step in funnel}
        self.assertLessEqual(by_id["consent"]["percent"], 100.0)
        for step_id in ("sent", "delivered", "opened", "clicked"):
            self.assertLessEqual(by_id[step_id]["percent"], 100.0)
        self.assertEqual(by_id["sent"]["percent"], 100.0)


class AggregateCountsTests(unittest.TestCase):
    def test_counts(self) -> None:
        rows = [
            _delivery_row("j", "1", "A", "a@x.ru", "rusender", "delivered"),
            _delivery_row("j", "2", "B", "b@x.ru", "rusender", "opened"),
            _delivery_row("j", "3", "C", "c@x.ru", "rusender", "clicked"),
            _delivery_row("j", "4", "D", "d@x.ru", "smtp", "hard_bounced", delivery_response="user unknown"),
        ]
        consents = [_consent_row("j", "1", "A", "a@x.ru")]
        counts = _aggregate_counts(rows, consents)
        self.assertEqual(counts["sent"], 4)
        self.assertEqual(counts["delivered"], 3)  # delivered + opened + clicked
        self.assertEqual(counts["opened"], 2)     # opened + clicked
        self.assertEqual(counts["clicked"], 1)
        self.assertEqual(counts["errors"], 1)
        self.assertEqual(counts["consents"], 1)
        self.assertEqual(counts["materials_sent"], 1)


class WorkListsAndInsightsTests(unittest.TestCase):
    def _rows(self):
        return [
            _delivery_row("j", "1", "Орг1", "a@x.ru", "rusender", "opened"),
            _delivery_row("j", "2", "Орг2", "b@x.ru", "smtp", "hard_bounced", delivery_response="user unknown"),
        ]

    def test_build_work_lists_keys(self) -> None:
        lists = build_work_lists(self._rows())
        self.assertIn("interested", lists)
        self.assertIn("email_problems", lists)

    def test_build_insights_returns_items(self) -> None:
        rows = self._rows()
        counts = _aggregate_counts(rows, [])
        insights = build_insights(rows=rows, counts=counts)
        self.assertTrue(insights)
        for item in insights:
            self.assertIn("title", item)
            self.assertIn("text", item)


class DashboardTests(unittest.TestCase):
    def test_dashboard_summary_rates_and_wording(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered"),
            _delivery_row("job-1", "2", "Орг2", "b@x.ru", "mailopost", "opened"),
            _delivery_row("job-1", "3", "Орг3", "c@x.ru", "smtp", "hard_bounced", delivery_response="user unknown"),
        ]
        consents = [_consent_row("job-1", "1", "Орг1", "a@x.ru")]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=consents):
            result = build_manager_dashboard(StatsFilters(job_ids=("job-1",)))
        self.assertEqual(result["summary"]["sent"], 3)
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertIn("delivery_rate", result["rates"])
        self.assertIn("open_rate", result["rates"])
        self.assertIn("ctr", result["rates"])
        self.assertIn("error_rate", result["rates"])
        funnel_labels = [step["label"] for step in result["funnels"]]
        self.assertIn("Переходы", funnel_labels)
        self.assertNotIn("Клик", funnel_labels)
        self.assertFalse(result["empty"])

    def test_dashboard_empty(self) -> None:
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=[]), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]):
            result = build_manager_dashboard(StatsFilters(job_ids=("job-1",)))
        self.assertTrue(result["empty"])


class RecipientsTests(unittest.TestCase):
    def _rows(self, n=25):
        return [
            _delivery_row("job-1", str(i), f"Орг{i}", f"user{i}@x.ru", "rusender", "delivered")
            for i in range(n)
        ]

    def test_pagination_and_fields(self) -> None:
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=self._rows(25)):
            result = build_recipients(StatsFilters(job_ids=("job-1",)), page=2, per_page=10)
        self.assertEqual(result["pagination"]["total"], 25)
        self.assertEqual(result["pagination"]["pages"], 3)
        self.assertEqual(result["pagination"]["page"], 2)
        self.assertEqual(len(result["items"]), 10)
        item = result["items"][0]
        for field in ("row_key", "organization", "recipient_name", "email", "role_label", "manager_status", "interest", "next_action"):
            self.assertIn(field, item)

    def test_per_page_cap_semantics(self) -> None:
        # The drill-down UI pages through 100-row chunks; ensure per_page=100 works.
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=self._rows(150)):
            result = build_recipients(StatsFilters(job_ids=("job-1",)), page=1, per_page=100)
        self.assertEqual(len(result["items"]), 100)
        self.assertEqual(result["pagination"]["pages"], 2)


class ConsentsTests(unittest.TestCase):
    def test_drilldown_fields_and_summary(self) -> None:
        rows = [
            _consent_row("job-1", "1", "Орг1", "a@x.ru", status_key="confirmed", materials="sent"),
            _consent_row("job-1", "2", "Орг2", "b@x.ru", status_key="pending", materials=""),
        ]
        with unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=[]):
            result = build_consents_view(StatsFilters(job_ids=("job-1",)))
        self.assertEqual(result["summary"]["confirmed"], 1)
        self.assertEqual(result["summary"]["materials_sent"], 1)
        item = result["items"][0]
        # Fields consumed by the drill-down modal columns must exist.
        for field in ("organization", "contact", "email", "consent_status_label", "materials_label", "last_action_label", "last_action_at", "interest", "next_action"):
            self.assertIn(field, item)
        funnel_labels = [step["label"] for step in result["funnel"]]
        self.assertEqual(funnel_labels, ["Согласие", "Материалы отправлены", "Открыли после согласия"])

    def test_row_key_attached_only_when_recipient_matches(self) -> None:
        """Consent rows reuse the recipient action flow via the delivery row_key,
        but only when the contact was actually sent to."""
        consents = [
            _consent_row("job-1", "1", "Орг1", "a@x.ru", status_key="confirmed", materials="sent"),
            _consent_row("job-1", "2", "Орг2", "b@x.ru", status_key="pending", materials=""),
        ]
        delivery = [_delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered")]
        with unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=consents), \
             unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=delivery):
            result = build_consents_view(StatsFilters(job_ids=("job-1",)))
        matched = next(item for item in result["items"] if item["email"] == "a@x.ru")
        unmatched = next(item for item in result["items"] if item["email"] == "b@x.ru")
        self.assertEqual(matched["row_key"], make_row_key("job-1", "1", "a@x.ru"))
        self.assertIsNone(unmatched.get("row_key"))


class EmailProblemsTests(unittest.TestCase):
    def test_counts(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered"),
            _delivery_row("job-1", "2", "Орг2", "b@x.ru", "smtp", "hard_bounced", delivery_response="user unknown"),
            _delivery_row("job-1", "3", "Орг3", "c@x.ru", "smtp", "soft_bounced"),
        ]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows):
            result = build_email_problems(StatsFilters(job_ids=("job-1",)))
        self.assertEqual(result["summary"]["problem_addresses"], 2)
        self.assertEqual(result["summary"]["hard_bounce"], 1)
        self.assertEqual(result["summary"]["soft_bounce"], 1)


class CampaignRecipientHistoryTests(unittest.TestCase):
    def test_history_uses_current_recipient_id_for_deletion(self) -> None:
        raw_row = {
            "row_id": "1",
            "recipient": "person@example.com",
            "mun_name": "Company",
            "provider_status": "sent",
        }
        lookup = {
            "campaign_id": "campaign-1",
            "by_id": {"42": 42},
            "by_source_row": {"1": 42},
            "by_email": {"person@example.com": 42},
        }
        with unittest.mock.patch.object(
            manager_stats, "_build_delivery_rows", return_value=([raw_row], {})
        ), unittest.mock.patch.object(
            manager_stats, "_campaign_recipient_lookup", return_value=lookup
        ), unittest.mock.patch.object(
            manager_stats, "latest_action_by_recipient", return_value={}
        ), unittest.mock.patch.object(
            manager_stats, "_load_company_data_for_job", return_value={}
        ):
            rows = manager_stats._build_delivery_rows_for_job("job-1", refresh=False)

        self.assertEqual(rows[0]["row_id"], "42")
        self.assertEqual(rows[0]["campaign_id"], "campaign-1")

    def test_deleted_recipient_history_is_hidden(self) -> None:
        raw_row = {
            "row_id": "1",
            "recipient": "deleted@example.com",
            "mun_name": "Deleted",
            "provider_status": "sent",
        }
        lookup = {
            "campaign_id": "campaign-1",
            "by_id": {},
            "by_source_row": {},
            "by_email": {},
        }
        with unittest.mock.patch.object(
            manager_stats, "_build_delivery_rows", return_value=([raw_row], {})
        ), unittest.mock.patch.object(
            manager_stats, "_campaign_recipient_lookup", return_value=lookup
        ), unittest.mock.patch.object(
            manager_stats, "latest_action_by_recipient", return_value={}
        ), unittest.mock.patch.object(
            manager_stats, "_load_company_data_for_job", return_value={}
        ):
            rows = manager_stats._build_delivery_rows_for_job("job-1", refresh=False)

        self.assertEqual(rows, [])

class CampaignsTests(unittest.TestCase):
    def test_campaigns_summary(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered"),
            _delivery_row("job-1", "2", "Орг2", "b@x.ru", "rusender", "opened"),
        ]
        for row in rows:
            row["campaign_id"] = "campaign-1"
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
             unittest.mock.patch.object(manager_stats, "_campaign_status", return_value="completed"), \
             unittest.mock.patch.object(manager_stats, "_load_campaign_statuses", return_value={}), \
             unittest.mock.patch.object(manager_stats, "_campaign_period", return_value=("2026-05-01", "2026-05-02")), \
             unittest.mock.patch.object(manager_stats, "_campaign_metadata", return_value={"title": "Кампания"}):
            result = build_campaigns(StatsFilters(job_ids=("job-1",)))
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["completed"], 1)
        self.assertEqual(len(result["campaigns"]), 1)
        campaign = result["campaigns"][0]
        self.assertEqual(campaign["title"], "Кампания")
        self.assertEqual(campaign["sent"], 2)
        self.assertEqual(campaign["campaign_id"], "campaign-1")
        self.assertTrue(campaign["can_delete"])
        self.assertIn("delivery_rate", campaign)
        self.assertIn("open_rate", campaign)
        self.assertEqual(campaign["period_label"], "01.05.2026 — 02.05.2026")

    def test_campaignflow_status_is_used_instead_of_stale_sender_state(self) -> None:
        with unittest.mock.patch(
            "src.campaigns.service.get_campaign_by_job_id",
            return_value={"status": "completed_with_errors"},
        ), unittest.mock.patch.object(
            manager_stats,
            "_load_sender_state",
            return_value={"status": "idle", "mode": "dry_run"},
        ):
            self.assertEqual(
                manager_stats._campaign_status("job-current"),
                "completed_with_errors",
            )

    def test_campaign_title_search_does_not_filter_delivery_rows(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "ООО Получатель", "a@x.ru", "rusender", "delivered"),
        ]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
             unittest.mock.patch.object(manager_stats, "_load_campaign_statuses", return_value={}), \
             unittest.mock.patch.object(manager_stats, "_campaign_status", return_value="completed"), \
             unittest.mock.patch.object(manager_stats, "_campaign_period", return_value=("2026-05-01", "2026-05-01")), \
             unittest.mock.patch.object(manager_stats, "_campaign_metadata", return_value={"title": "Весенняя рассылка"}):
            result = build_campaigns(StatsFilters(job_ids=("job-1",), q="весенняя"))

        self.assertEqual(len(result["campaigns"]), 1)
        self.assertEqual(result["campaigns"][0]["sent"], 1)

    def test_period_and_provider_filters_hide_campaigns_without_matching_sends(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered"),
        ]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
             unittest.mock.patch.object(manager_stats, "_load_campaign_statuses", return_value={}), \
             unittest.mock.patch.object(manager_stats, "_campaign_status", return_value="completed"), \
             unittest.mock.patch.object(manager_stats, "_campaign_metadata", return_value={"title": "Кампания"}):
            result = build_campaigns(
                StatsFilters(job_ids=("job-1",), providers=("smtp",)),
            )

        self.assertEqual(result["campaigns"], [])
        self.assertEqual(result["summary"]["total"], 0)


class CampaignAnalyticsTests(unittest.TestCase):
    def test_generic_delivery_failure_uses_status_label_as_error(self) -> None:
        label = manager_stats._delivery_failure_error_label(
            {"bounce_reason": "other", "bounce_reason_label": "Прочее"},
            normalize_manager_status("err_delivery_failed"),
        )

        self.assertEqual(label, "Ошибка доставки")

    def test_delivery_failure_includes_provider_response(self) -> None:
        label = manager_stats._delivery_failure_error_label(
            {
                "bounce_reason": "email_not_exists",
                "bounce_reason_label": "Email не существует",
                "delivery_response": "550 5.1.1 user unknown",
            },
            normalize_manager_status("err_delivery_failed"),
        )

        self.assertEqual(
            label,
            "Email не существует: 550 5.1.1 user unknown",
        )

    def test_generic_delivery_failure_includes_provider_response(self) -> None:
        label = manager_stats._delivery_failure_error_label(
            {
                "bounce_reason": "other",
                "bounce_reason_label": "Прочее",
                "delivery_response": "554 transaction failed",
            },
            normalize_manager_status("err_delivery_failed"),
        )

        self.assertEqual(label, "Ошибка доставки: 554 transaction failed")

    def test_attempt_union_deduplicates_sent_log_by_provider_message_id(self) -> None:
        database_attempts = [
            {
                "row_id": "1",
                "delivery_email": "sent@x.ru",
                "status": "sent",
                "provider_message_id": "provider-1",
            },
            {
                "row_id": "2",
                "delivery_email": "failed@x.ru",
                "status": "failed",
                "error": "temporary",
            },
        ]
        sent_log = [
            {
                "row_id": "1",
                "recipient": "sent@x.ru",
                "provider_message_id": "provider-1",
            },
            {
                "row_id": "3",
                "recipient": "legacy@x.ru",
                "provider_message_id": "provider-2",
            },
        ]

        unmatched = manager_stats._unmatched_sent_log_indexes(
            database_attempts,
            sent_log,
        )

        self.assertEqual(unmatched, [1])

    def test_current_campaign_attempts_do_not_add_extra_sent_log_rows(self) -> None:
        database_attempts = [
            {
                "id": 1,
                "row_id": "1",
                "delivery_email": "primary@x.ru",
                "status": "sent",
                "provider_message_id": "provider-1",
                "organization": "Орг1",
            }
        ]
        sent_log = [
            {
                "row_id": "1",
                "recipient": "primary@x.ru",
                "status": "sent",
                "provider_message_id": "provider-1",
            },
            {
                "row_id": "1",
                "recipient": "fallback@x.ru",
                "status": "sent",
                "provider_message_id": "provider-2",
            },
        ]

        with unittest.mock.patch.object(
            manager_stats,
            "_load_campaign_delivery_attempts",
            return_value=("campaign-1", database_attempts),
        ), unittest.mock.patch.object(
            manager_stats,
            "_load_delivery_for_jobs",
            return_value=[],
        ), unittest.mock.patch(
            "src.jobs.job_docs.read_sent_mail_log",
            return_value=sent_log,
        ):
            attempts = manager_stats._campaign_attempt_rows("job-1")
            total = manager_stats._campaign_attempt_total("job-1")

        self.assertEqual(len(attempts), 1)
        self.assertEqual(total, 1)
        self.assertEqual(attempts[0]["provider_message_id"], "provider-1")

    def test_legacy_campaign_attempts_fall_back_to_sent_log(self) -> None:
        sent_log = [
            {
                "row_id": "1",
                "recipient": "legacy@x.ru",
                "status": "sent",
                "provider_message_id": "provider-1",
            }
        ]

        with unittest.mock.patch.object(
            manager_stats,
            "_load_campaign_delivery_attempts",
            return_value=("", []),
        ), unittest.mock.patch.object(
            manager_stats,
            "_load_delivery_for_jobs",
            return_value=[],
        ), unittest.mock.patch(
            "src.jobs.job_docs.read_sent_mail_log",
            return_value=sent_log,
        ):
            attempts = manager_stats._campaign_attempt_rows("job-legacy")
            total = manager_stats._campaign_attempt_total("job-legacy")

        self.assertEqual(len(attempts), 1)
        self.assertEqual(total, 1)

    def test_problem_addresses_carry_row_key_and_org(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered"),
            _delivery_row("job-1", "2", "Орг2", "b@x.ru", "smtp", "hard_bounced", delivery_response="user unknown"),
        ]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
             unittest.mock.patch.object(manager_stats, "_trigger_provider_refresh", return_value=(False, False)), \
             unittest.mock.patch.object(manager_stats, "_campaign_metadata", return_value={"title": "Кампания"}), \
             unittest.mock.patch.object(manager_stats, "_campaign_period", return_value=("2026-05-01", "2026-05-02")), \
             unittest.mock.patch.object(manager_stats, "_campaign_status", return_value="completed"), \
             unittest.mock.patch.object(manager_stats, "_campaign_attempt_total", return_value=3), \
             unittest.mock.patch("src.campaigns.service.get_campaign_by_job_id", return_value=None):
            result = build_campaign_analytics("job-1")
        self.assertTrue(result["problem_addresses"], "Expected at least one problem address")
        problem = result["problem_addresses"][0]
        self.assertEqual(problem["email"], "b@x.ru")
        self.assertEqual(problem["organization"], "Орг2")
        self.assertEqual(problem["row_key"], make_row_key("job-1", "2", "b@x.ru"))
        self.assertEqual(result["summary"]["total_attempts"], 3)
        self.assertEqual(result["summary"]["not_sent"], 1)
        self.assertEqual(result["summary"]["provider_errors"], 1)
        self.assertFalse(result["link_analytics"]["has_links"])


class CampaignFullAnalyticsTests(unittest.TestCase):
    def test_full_analytics_returns_core_sections(self) -> None:
        rows = [_delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered")]
        sent_log = [{"sent_at": "2026-05-01", "recipient": "a@x.ru", "status": "sent"}]
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows), \
             unittest.mock.patch.object(manager_stats, "_load_companies_for_jobs", return_value=_group_rows_into_companies(rows)), \
             unittest.mock.patch.object(manager_stats, "_load_consents_for_jobs", return_value=[]), \
             unittest.mock.patch.object(manager_stats, "_trigger_provider_refresh", return_value=(False, False)), \
             unittest.mock.patch.object(manager_stats, "_campaign_metadata", return_value={"title": "Кампания"}), \
             unittest.mock.patch.object(manager_stats, "_campaign_period", return_value=("2026-05-01", "2026-05-02")), \
             unittest.mock.patch.object(manager_stats, "_campaign_status", return_value="completed"), \
             unittest.mock.patch.object(manager_stats, "_campaign_attempt_total", return_value=1), \
             unittest.mock.patch("src.jobs.job_docs.read_sent_mail_log", return_value=sent_log), \
             unittest.mock.patch("src.campaigns.service.get_campaign_by_job_id", return_value=None), \
             unittest.mock.patch.object(manager_stats, "build_domain_delivery_stats", return_value={"providers": []}):
            result = build_campaign_full_analytics("job-1")
        for key in (
            "summary",
            "rates",
            "operational",
            "delivery",
            "domain_stats",
            "delivery_rows",
            "sent_mail_log",
            "documents",
            "recipients",
        ):
            self.assertIn(key, result)
        self.assertIn("pending_rate", result["rates"])
        self.assertEqual(result["sent_mail_log"]["pagination"]["total"], 1)


class CompanyAggregationTests(unittest.TestCase):
    """Statistics group per-email rows into one row per company (row_id)."""

    def test_best_status_wins_across_emails(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "primary@x.ru", "rusender", "delivered", role="primary"),
            _delivery_row(
                "job-1", "1", "Орг1", "backup@x.ru", "smtp", "hard_bounced", role="fallback",
                delivery_response="user unknown",
            ),
        ]
        companies = _group_rows_into_companies(rows)
        self.assertEqual(len(companies), 1)
        company = companies[0]
        # Delivered beats a hard bounce: the company counts as reached.
        self.assertEqual(company["manager_status"]["key"], "delivered")
        self.assertEqual(company["email_count"], 2)
        self.assertEqual(company["attempts"], 2)
        self.assertEqual(len(company["emails"]), 2)
        # Manager actions target the primary email.
        self.assertEqual(company["row_key"], make_row_key("job-1", "1", "primary@x.ru"))

    def test_company_placeholders(self) -> None:
        view = _company_view(None, "1", "Орг1")
        self.assertEqual(view["name"], "Орг1")
        self.assertEqual(view["fields"]["region"]["display"], COMPANY_DATA_PLACEHOLDER)
        self.assertFalse(view["fields"]["region"]["present"])
        self.assertEqual(view["fields"]["inn"]["display"], COMPANY_DATA_PLACEHOLDER)

    def test_company_name_falls_back_to_row_id(self) -> None:
        view = _company_view(None, "42", "")
        self.assertEqual(view["name"], "Компания №42")

    def test_recipients_expose_company_and_emails(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "Орг1", "a@x.ru", "rusender", "delivered", role="primary"),
            _delivery_row("job-1", "1", "Орг1", "b@x.ru", "rusender", "opened", role="fallback"),
        ]
        for row in rows:
            row["campaign_id"] = "campaign-1"
        with unittest.mock.patch.object(manager_stats, "_load_delivery_for_jobs", return_value=rows):
            result = build_recipients(StatsFilters(job_ids=("job-1",)))
        self.assertEqual(result["pagination"]["total"], 1)
        item = result["items"][0]
        self.assertIn("company", item)
        self.assertIn("emails", item)
        self.assertEqual(len(item["emails"]), 2)
        self.assertEqual(item["company"]["fields"]["region"]["display"], COMPANY_DATA_PLACEHOLDER)
        # opened outranks delivered, so the company's best status is "opened".
        self.assertEqual(item["manager_status"]["key"], "opened")
        self.assertEqual(item["campaign_id"], "campaign-1")
        self.assertTrue(item["can_delete"])

    def test_campaign_attempts_group_only_the_current_campaign_by_company(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "ООО Альфа", "one@alpha.ru", "rusender", "delivered"),
            _delivery_row("job-1", "1", "ООО Альфа", "two@alpha.ru", "rusender", "opened"),
        ]
        companies = _group_rows_into_companies(rows)
        attempts = [
            {
                "id": 1,
                "row_id": "1",
                "email": "one@alpha.ru",
                "status": "sent",
                "status_label": "Принято провайдером",
                "provider_label": "RuSender",
                "provider_message_id": "msg-1",
                "updated_at": "2026-05-01T10:00:00",
                "manager_status": normalize_manager_status("delivered"),
            },
            {
                "id": 2,
                "row_id": "1",
                "email": "two@alpha.ru",
                "status": "sent",
                "status_label": "Принято провайдером",
                "provider_label": "RuSender",
                "provider_message_id": "msg-2",
                "updated_at": "2026-05-01T10:01:00",
                "manager_status": normalize_manager_status("opened"),
            },
            {
                "id": 3,
                "row_id": "1",
                "email": "two@alpha.ru",
                "status": "failed",
                "status_label": "Ошибка",
                "provider_label": "RuSender",
                "error": "temporary",
                "updated_at": "2026-05-01T09:59:00",
                "manager_status": normalize_manager_status("failed"),
            },
            {
                "id": 4,
                "row_id": "1",
                "email": "one@alpha.ru",
                "status": "sent",
                "status_label": "Принято провайдером",
                "provider_label": "RuSender",
                "provider_message_id": "msg-3",
                "updated_at": "2026-05-01T10:02:00",
                "manager_status": normalize_manager_status("err_delivery_failed"),
            },
        ]

        with unittest.mock.patch.object(
            manager_stats,
            "_load_companies_for_jobs",
            return_value=companies,
        ), unittest.mock.patch.object(
            manager_stats,
            "_campaign_attempt_rows",
            return_value=attempts,
        ):
            result = build_campaign_attempts("job-1", page=1, per_page=20)

        self.assertEqual(result["summary"]["companies"], 1)
        self.assertEqual(result["summary"]["total_attempts"], 4)
        self.assertEqual(result["summary"]["sent"], 3)
        self.assertEqual(result["summary"]["accepted_recipients"], 1)
        self.assertEqual(result["summary"]["delivered"], 1)
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertEqual(result["summary"]["send_errors"], 1)
        self.assertEqual(result["summary"]["provider_errors"], 0)
        self.assertEqual(result["summary"]["pending"], 0)
        item = result["items"][0]
        self.assertEqual(item["attempts_total"], 4)
        self.assertEqual(item["sent_count"], 3)
        self.assertEqual(item["delivered_count"], 1)
        self.assertEqual(item["error_count"], 1)
        self.assertEqual(item["pending_count"], 0)
        self.assertEqual(item["email_count"], 2)
        self.assertEqual(item["organization"], "ООО Альфа")

    def test_send_failure_is_not_counted_again_as_provider_delivery_error(self) -> None:
        attempts = [
            {
                "id": 1,
                "row_id": "1",
                "email": "failed@alpha.ru",
                "status": "failed",
                "error": "provider request was not accepted",
                "manager_status": normalize_manager_status("failed"),
            }
        ]

        with unittest.mock.patch.object(
            manager_stats,
            "_load_companies_for_jobs",
            return_value=[],
        ), unittest.mock.patch.object(
            manager_stats,
            "_campaign_attempt_rows",
            return_value=attempts,
        ):
            result = build_campaign_attempts("job-1", page=1, per_page=20)

        self.assertEqual(result["summary"]["errors"], 1)
        self.assertEqual(result["summary"]["send_errors"], 1)
        self.assertEqual(result["summary"]["provider_errors"], 0)

    def test_company_detail_contains_current_campaign_attempts_emails_and_documents(self) -> None:
        rows = [
            _delivery_row("job-1", "1", "ООО Альфа", "one@alpha.ru", "rusender", "delivered"),
        ]
        companies = _group_rows_into_companies(rows)
        attempts = [{"id": 1, "row_id": "1", "email": "one@alpha.ru", "status": "sent"}]
        sent_emails = [{"row_id": "1", "email": "one@alpha.ru", "subject": "Первое письмо"}]
        documents = [{"job_id": "job-1", "path": "1/offer.pdf", "label": "КП"}]

        with unittest.mock.patch.object(
            manager_stats,
            "_load_companies_for_jobs",
            return_value=companies,
        ), unittest.mock.patch.object(
            manager_stats,
            "_company_documents",
            return_value=documents,
        ), unittest.mock.patch.object(
            manager_stats,
            "_company_sent_emails",
            return_value=sent_emails,
        ), unittest.mock.patch.object(
            manager_stats,
            "_campaign_attempt_rows",
            return_value=attempts,
        ), unittest.mock.patch.object(
            manager_stats,
            "_load_consents_for_jobs",
            return_value=[],
        ), unittest.mock.patch.object(
            manager_stats,
            "load_manager_actions",
            return_value=[],
        ):
            detail = build_recipient_detail(str(companies[0]["row_key"]))

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["job_id"], "job-1")
        self.assertEqual(detail["summary"]["attempts"], 1)
        self.assertEqual(detail["summary"]["accepted"], 1)
        self.assertEqual(detail["summary"]["delivered"], 1)
        self.assertEqual(detail["summary"]["errors"], 0)
        self.assertEqual(detail["summary"]["pending"], 1)
        self.assertEqual(detail["summary"]["sent_emails"], 1)
        self.assertEqual(detail["summary"]["documents"], 1)
        self.assertEqual(detail["sent_emails"][0]["subject"], "Первое письмо")


class ReportsViewTests(unittest.TestCase):
    def test_available_reports(self) -> None:
        available = list_available_reports()
        self.assertGreaterEqual(len(available), 4)
        for item in available:
            self.assertIn("id", item)
            self.assertIn("title", item)

    def test_reports_history_summary(self) -> None:
        history = [
            {"report_id": "r1", "format": "xlsx", "created_at": "2026-05-01"},
            {"report_id": "r2", "format": "csv", "created_at": "2026-05-02"},
        ]
        with unittest.mock.patch.object(manager_stats, "load_report_history", return_value=history):
            result = build_reports_view(("job-1",))
        self.assertEqual(result["summary"]["generated"], 2)
        self.assertEqual(result["summary"]["xlsx"], 1)
        self.assertEqual(result["summary"]["csv"], 1)


if __name__ == "__main__":
    unittest.main()
