from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from src.generator.delivery import sender_agent
from src.generator.delivery import sender_report
from src.web import sender_service


class SenderAgentScalabilityTests(unittest.TestCase):
    def test_state_rows_snapshot_keeps_recent_entries(self) -> None:
        rows = [{"id": index} for index in range(sender_agent.SENDER_STATE_ROWS_LIMIT + 25)]

        snapshot = sender_agent._state_rows_snapshot(rows)

        self.assertEqual(len(snapshot), sender_agent.SENDER_STATE_ROWS_LIMIT)
        self.assertEqual(snapshot[0]["id"], 25)
        self.assertEqual(snapshot[-1]["id"], sender_agent.SENDER_STATE_ROWS_LIMIT + 24)

    def test_should_flush_sender_workbook_every_batch_and_on_finish(self) -> None:
        self.assertFalse(
            sender_agent._should_flush_sender_workbook(dirty=False, processed_rows=25, total_rows=100)
        )
        self.assertTrue(
            sender_agent._should_flush_sender_workbook(
                dirty=True,
                processed_rows=sender_agent.SENDER_WORKBOOK_SAVE_EVERY,
                total_rows=100,
            )
        )
        self.assertTrue(
            sender_agent._should_flush_sender_workbook(dirty=True, processed_rows=100, total_rows=100)
        )

    def test_unisender_parallel_workers_only_for_real_unisender_send(self) -> None:
        with patch.object(sender_agent.settings, "sender_unisender_concurrency", 7):
            self.assertEqual(sender_agent._unisender_parallel_workers(dry_run=False, transport="unisender"), 7)
            self.assertEqual(sender_agent._unisender_parallel_workers(dry_run=True, transport="unisender"), 1)
            self.assertEqual(sender_agent._unisender_parallel_workers(dry_run=False, transport="smtp"), 1)

    def test_sent_mail_recipients_are_scoped_to_current_send_run(self) -> None:
        item = {"send_run_id": "send-new", "sent_at": "2026-06-02T12:00:00"}

        self.assertTrue(
            sender_agent._sent_log_item_in_send_scope(
                item,
                send_run_id="send-new",
                send_run_started_at="2026-06-02T12:00:00",
            )
        )
        self.assertFalse(
            sender_agent._sent_log_item_in_send_scope(
                item,
                send_run_id="send-old",
                send_run_started_at="2026-06-02T12:00:00",
            )
        )

    def test_unisender_analytics_filters_current_send_run_items(self) -> None:
        items = [
            {"send_run_id": "send-old", "sent_at": "2026-06-01T10:00:00", "row_id": "1"},
            {"send_run_id": "send-new", "sent_at": "2026-06-02T10:00:00", "row_id": "2"},
        ]

        scoped = sender_report._filter_items_by_send_scope(
            items,
            send_run_id="send-new",
            send_run_started_at="2026-06-02T09:00:00",
        )

        self.assertEqual([item["row_id"] for item in scoped], ["2"])

    def test_unisender_analytics_filters_by_complete_sender_state(self) -> None:
        items = [
            {"row_id": "1", "recipient": "one@example.com"},
            {"row_id": "1", "recipient": "old-one@example.com"},
            {"row_id": "2", "recipient": "two@example.com"},
        ]
        state = {
            "total_rows": 2,
            "rows": [
                {"id": "1", "sent_recipients": ["one@example.com"]},
                {"id": "2", "recipient": "two@example.com"},
            ],
        }

        with patch.object(sender_report, "_load_sender_state", return_value=state):
            scoped = sender_report._filter_items_by_current_sender_state("job-current", items)

        self.assertEqual(
            [(item["row_id"], item["recipient"]) for item in scoped],
            [("1", "one@example.com"), ("2", "two@example.com")],
        )

    def test_unisender_analytics_does_not_filter_by_partial_sender_state(self) -> None:
        items = [
            {"row_id": "1", "recipient": "one@example.com"},
            {"row_id": "2", "recipient": "two@example.com"},
        ]
        state = {"total_rows": 3, "rows": [{"id": "1", "sent_recipients": ["one@example.com"]}]}

        with patch.object(sender_report, "_load_sender_state", return_value=state):
            scoped = sender_report._filter_items_by_current_sender_state("job-current", items)

        self.assertEqual(scoped, items)

    def test_sender_analytics_does_not_filter_by_scoped_consent_followup_state(self) -> None:
        items = [
            {"row_id": "1", "recipient": "one@example.com"},
            {"row_id": "2", "recipient": "two@example.com"},
        ]
        state = {
            "selection_scoped": True,
            "total_rows": 1,
            "rows": [{"id": "2", "recipient": "two@example.com"}],
        }

        with patch.object(sender_report, "_load_sender_state", return_value=state):
            scoped = sender_report._filter_items_by_current_sender_state("job-current", items)

        self.assertEqual(scoped, items)

    def test_compact_sender_status_shows_table_totals_after_scoped_material_send(self) -> None:
        state = {
            "status": "completed",
            "mode": "send",
            "send_mode": "materials",
            "selection_scoped": True,
            "processed_rows": 1,
            "ready_rows": 1,
            "sent_rows": 1,
            "error_rows": 0,
            "total_rows": 1,
            "remaining_rows": 0,
            "stats": {"total": 2, "sent": 2, "error": 0, "pending": 0},
        }

        compact = sender_service.compact_sender_status(state)

        self.assertEqual(compact["total_rows"], 2)
        self.assertEqual(compact["sent_rows"], 2)
        self.assertEqual(compact["processed_rows"], 2)
        self.assertEqual(compact["ready_rows"], 2)
        self.assertEqual(compact["stats"], {"total": 2, "sent": 2, "error": 0, "pending": 0})

    def test_materials_send_requires_confirmed_consent_even_when_caller_omits_flag(self) -> None:
        row = {
            "ID": "1",
            "_row_index": 2,
            "MUN_NAME": "Test municipality",
            "EMAIL_OSN": "recipient@example.com",
            "EMAIL_DOP": "",
            "STATUS": "",
        }

        def load_state(job_id: str | None = None) -> dict:
            state = dict(sender_agent.SENDER_STATE)
            state["rows"] = []
            state["stats"] = {}
            return state

        with patch.object(sender_agent, "_load_sender_state", side_effect=load_state), patch.object(
            sender_agent, "_save_sender_state", side_effect=lambda state, job_id=None: state
        ), patch.object(
            sender_agent, "_resolve_sender_data_xlsx_path", return_value=Path(__file__)
        ), patch.object(
            sender_agent, "_collect_excel_stats", return_value={"total": 1, "sent": 0, "error": 0, "pending": 1}
        ), patch.object(
            sender_agent, "load_rows", return_value=(SimpleNamespace(close=lambda: None), SimpleNamespace(), [row])
        ), patch.object(
            sender_agent, "_build_output_folder_index", return_value=({}, {})
        ), patch.object(
            sender_agent, "has_confirmed_consent", return_value=False
        ) as has_confirmed_consent, patch.object(
            sender_agent, "_resolve_output_folder"
        ) as resolve_output_folder, patch.object(
            sender_agent, "count_tasks_for_agent", return_value={}
        ), patch.object(
            sender_agent, "get_tasks_for_agent", return_value=[]
        ), patch.object(
            sender_agent, "get_recent_events", return_value=[]
        ):
            result = sender_agent.run_sender(
                dry_run=True,
                send_mode="materials",
                job_id="job-consent",
            )

        self.assertEqual(result["error_rows"], 1)
        self.assertEqual(result["rows"][0]["result"], "blocked_no_consent")
        self.assertIn("согласия", result["rows"][0]["error"])
        has_confirmed_consent.assert_called_once_with(
            job_id="job-consent",
            row_id="1",
            recipient="recipient@example.com",
            attachment_mode="kp",
        )
        resolve_output_folder.assert_not_called()

    def test_rusender_uses_bearer_authorization_for_current_keys(self) -> None:
        captured: dict[str, Request] = {}

        def fake_request(request: Request, *, timeout: float) -> str:
            captured["request"] = request
            return json.dumps({"uuid": "message-1"})

        with patch.object(sender_agent.settings, "rusender_api_key", "rs_ck_v1_secret"), patch.object(
            sender_agent.settings,
            "rusender_sender_email",
            "sender@example.com",
        ), patch.object(sender_agent, "_run_rusender_request", side_effect=fake_request):
            result = sender_agent._send_via_rusender(
                {"ID": "1", "MUN_NAME": "Тестовое МО"},
                "recipient@example.com",
                [],
                "Тема",
                body_override="Текст письма",
                job_id="job-test",
                send_run_id="send-test",
                send_mode="consent_request",
                attachment_mode="kp",
            )

        request = captured["request"]
        self.assertEqual(request.get_header("Authorization"), "Bearer rs_ck_v1_secret")
        self.assertIsNone(request.get_header("X-api-key"))
        self.assertEqual(result["message_id"], "message-1")

    def test_rusender_uses_x_api_key_for_legacy_jwt_keys(self) -> None:
        captured: dict[str, Request] = {}

        def fake_request(request: Request, *, timeout: float) -> str:
            captured["request"] = request
            return json.dumps({"uuid": "message-1"})

        legacy_key = "eyJhbGciOiJIUzI1NiJ9.test"
        with patch.object(sender_agent.settings, "rusender_api_key", legacy_key), patch.object(
            sender_agent.settings,
            "rusender_sender_email",
            "sender@example.com",
        ), patch.object(sender_agent, "_run_rusender_request", side_effect=fake_request):
            result = sender_agent._send_via_rusender(
                {"ID": "1", "MUN_NAME": "Тестовое МО"},
                "recipient@example.com",
                [],
                "Тема",
                body_override="Текст письма",
                job_id="job-test",
                send_run_id="send-test",
                send_mode="consent_request",
                attachment_mode="kp",
            )

        request = captured["request"]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_header("X-api-key"), legacy_key)
        self.assertEqual(result["message_id"], "message-1")

    def test_provider_idempotency_key_is_deterministic_for_same_context(self) -> None:
        first = sender_agent._build_provider_idempotency_key(
            provider="rusender",
            job_id="job-1",
            send_run_id="send-1",
            row_id="42",
            recipient="Recipient@Example.com",
            send_mode="materials",
            attachment_mode="kp",
        )
        second = sender_agent._build_provider_idempotency_key(
            provider="rusender",
            job_id="job-1",
            send_run_id="send-1",
            row_id="42",
            recipient="recipient@example.com",
            send_mode="materials",
            attachment_mode="kp",
        )

        self.assertEqual(first, second)
        self.assertRegex(first, r"^mailing-agent:rusender:[0-9a-f]{40}$")

    def test_provider_idempotency_key_changes_for_recipient_or_mode(self) -> None:
        base_kwargs = {
            "provider": "unisender_go",
            "job_id": "job-1",
            "send_run_id": "send-1",
            "row_id": "42",
            "recipient": "one@example.com",
            "send_mode": "materials",
            "attachment_mode": "kp",
        }

        base = sender_agent._build_provider_idempotency_key(**base_kwargs)

        self.assertNotEqual(
            base,
            sender_agent._build_provider_idempotency_key(**{**base_kwargs, "recipient": "two@example.com"}),
        )
        self.assertNotEqual(
            base,
            sender_agent._build_provider_idempotency_key(**{**base_kwargs, "send_mode": "consent_request"}),
        )
        self.assertNotEqual(
            base,
            sender_agent._build_provider_idempotency_key(**{**base_kwargs, "attachment_mode": "all"}),
        )

    def test_sent_mail_log_promotes_provider_idempotency_key(self) -> None:
        log_path = Path("tmp") / "test_sender_agent_provider_idempotency.jsonl"
        log_path.parent.mkdir(exist_ok=True)
        log_path.unlink(missing_ok=True)
        self.addCleanup(lambda: log_path.unlink(missing_ok=True))

        warning = sender_agent._append_sent_mail_log(
            row={"ID": "42", "MUN_NAME": "Test municipality"},
            recipient="recipient@example.com",
            attachments=[],
            subject="Subject",
            transport="rusender",
            provider={
                "provider": "rusender",
                "message_id": "message-1",
                "idempotency_key": "mailing-agent:rusender:stable",
            },
            sent_mail_log_path=log_path,
            send_run_id="send-1",
            send_run_started_at="2026-06-21T10:00:00",
        )

        self.assertIsNone(warning)
        record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["provider_idempotency_key"], "mailing-agent:rusender:stable")
        self.assertEqual(record["provider"]["idempotency_key"], "mailing-agent:rusender:stable")

    def test_unisender_analytics_filters_items_outside_current_data(self) -> None:
        items = [
            {"row_id": "1", "recipient": "one@example.com"},
            {"row_id": "2", "recipient": "two@example.com"},
            {"row_id": "2", "recipient": "old-two@example.com"},
            {"row_id": "101", "recipient": "old@example.com"},
        ]

        with patch.object(
            sender_report,
            "resolve_job_paths",
            return_value=SimpleNamespace(data_xlsx=Path(__file__)),
        ), patch.object(
            sender_report,
            "load_rows",
            return_value=(
                None,
                None,
                [
                    {"ID": "1", "EMAIL_OSN": "one@example.com"},
                    {"ID": "2", "EMAIL_OSN": "two@example.com"},
                ],
            ),
        ):
            scoped = sender_report._filter_items_by_current_data("job-current", items)

        self.assertEqual([item["row_id"] for item in scoped], ["1", "2"])
        self.assertEqual([item["recipient"] for item in scoped], ["one@example.com", "two@example.com"])

    def test_unisender_analytics_deduplicates_latest_row_recipient_item(self) -> None:
        items = [
            {"row_id": "1", "recipient": "one@example.com", "provider_job_id": "old"},
            {"row_id": "1", "recipient": "one@example.com", "provider_job_id": "new"},
        ]

        deduped = sender_report._dedupe_latest_log_items(items)

        self.assertEqual(deduped, [items[1]])

    def test_unisender_go_event_prefers_row_recipient_over_provider_job_fallback(self) -> None:
        events = {
            "provider_job:new-provider-id": {"provider_status": "delivered"},
            "row_email:1:test@example.com": {"provider_status": "hard_bounced"},
        }
        item = {
            "row_id": "1",
            "recipient": "test@example.com",
            "provider_job_id": "new-provider-id",
        }

        matched = sender_report._match_unisender_go_event(item, events)

        self.assertEqual(matched, {"provider_status": "hard_bounced"})

    def test_unisender_go_event_uses_provider_job_fallback_without_recipient(self) -> None:
        events = {
            "provider_job:new-provider-id": {"provider_status": "delivered"},
        }
        item = {
            "row_id": "1",
            "recipient": "",
            "provider_job_id": "new-provider-id",
        }

        matched = sender_report._match_unisender_go_event(item, events)

        self.assertEqual(matched, {"provider_status": "delivered"})

    def test_unisender_go_event_prefers_provider_job_and_recipient(self) -> None:
        events = {
            "provider_job:shared-provider-id": {"provider_status": "hard_bounced"},
            "provider_job_email:shared-provider-id:first@example.com": {"provider_status": "delivered"},
            "provider_job_email:shared-provider-id:second@example.com": {"provider_status": "err_user_unknown"},
        }

        first = sender_report._match_unisender_go_event(
            {
                "row_id": "1",
                "recipient": "first@example.com",
                "provider_job_id": "shared-provider-id",
            },
            events,
        )
        second = sender_report._match_unisender_go_event(
            {
                "row_id": "1",
                "recipient": "second@example.com",
                "provider_job_id": "shared-provider-id",
            },
            events,
        )

        self.assertEqual(first, {"provider_status": "delivered"})
        self.assertEqual(second, {"provider_status": "err_user_unknown"})

    def test_unisender_go_technical_spam_skip_is_not_hard_bounce(self) -> None:
        with patch.object(
            sender_report,
            "_build_delivery_rows",
            return_value=(
                [
                    {
                        "provider": "unisender_go",
                        "provider_status": "err_spam_skipped",
                        "checked_at": "2026-06-04T10:00:00",
                    }
                ],
                "",
            ),
        ):
            analytics = sender_report.build_sender_delivery_analytics("job-current", refresh=False)

        self.assertEqual(analytics["summary"]["hard_bounced"], 0)
        self.assertEqual(analytics["summary"]["errors"], 1)

    def test_sender_analytics_uses_rusender_provider_label_and_events(self) -> None:
        with patch.object(
            sender_report,
            "_build_delivery_rows",
            return_value=(
                [
                    {
                        "provider": "rusender",
                        "provider_status": "delivered",
                        "checked_at": "2026-06-18T12:10:00",
                    },
                    {
                        "provider": "rusender",
                        "provider_status": "clicked",
                        "checked_at": "2026-06-18T12:11:00",
                    },
                ],
                "",
            ),
        ):
            analytics = sender_report.build_sender_delivery_analytics("job-current", refresh=False)

        self.assertEqual(analytics["provider"], "rusender")
        self.assertEqual(analytics["provider_label"], "RuSender")
        self.assertEqual(analytics["summary"]["delivered"], 2)
        self.assertEqual(analytics["summary"]["clicked"], 1)
        self.assertEqual(analytics["provider_events_count"], 2)
        self.assertEqual(analytics["cards"][0]["title"], "Передано в RuSender")

    def test_run_unisender_request_retries_temporary_network_error(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(request: Request, timeout: float = 60):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise URLError("temporary network issue")

            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return b'{"status":"success"}'

            return _Response()

        with patch.object(sender_agent, "urlopen", side_effect=fake_urlopen), patch.object(
            sender_agent, "_sleep_sender_retry", return_value=None
        ):
            raw = sender_agent._run_unisender_request(
                Request("https://example.com"),
                timeout=5,
                request_label="UniSender Go",
            )

        self.assertEqual(raw, '{"status":"success"}')
        self.assertEqual(attempts["count"], 3)

    def test_run_unisender_request_keeps_last_http_error_body(self) -> None:
        def raise_http_error(request: Request, timeout: float = 60):
            raise HTTPError(
                url="https://example.com",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"temporary outage"}'),
            )

        with patch.object(sender_agent, "urlopen", side_effect=raise_http_error), patch.object(
            sender_agent, "_sleep_sender_retry", return_value=None
        ):
            with self.assertRaises(HTTPError) as caught:
                sender_agent._run_unisender_request(
                    Request("https://example.com"),
                    timeout=5,
                    request_label="UniSender Go",
                )

        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(getattr(caught.exception, "raw_body", ""), '{"message":"temporary outage"}')


if __name__ == "__main__":
    unittest.main()
