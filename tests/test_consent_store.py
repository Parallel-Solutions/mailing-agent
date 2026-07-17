from __future__ import annotations

import shutil
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.delivery import consent_store
from src.web import consent_router
from src.web.consent_router import create_consent_router
from tests.bootstrap import reset_test_database


@contextmanager
def _workspace_temp_dir() -> Iterator[Path]:
    root = Path(__file__).resolve().parents[1] / f"test-consent-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ConsentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_prepare_fails_when_token_is_not_persisted(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-persist-check" / "state" / "consents.json"
            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_save_records", return_value=None),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                with self.assertRaisesRegex(RuntimeError, "not persisted"):
                    consent_store.prepare_consent_request(
                        job_id="job-persist-check",
                        row={"ID": 42},
                        recipient="user@example.com",
                        transport="smtp",
                    )

    def test_prepare_and_confirm_consent(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    sender_email="sender@example.com",
                    connection_id="connection-1",
                    owner_username="admin",
                )

                self.assertIn("/consent/confirm/", record["consent_url"])
                self.assertEqual(record["sender_email"], "sender@example.com")
                self.assertEqual(record["connection_id"], "connection-1")
                self.assertEqual(record["owner_username"], "admin")
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )

                confirmed = consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")

                self.assertIsNotNone(confirmed)
                consent_document_path = Path(confirmed["consent_document_path"])
                if not consent_document_path.is_absolute():
                    consent_document_path = job_dir / consent_document_path
                self.assertTrue(consent_document_path.exists())
                self.assertTrue(consent_document_path.is_file())
                self.assertTrue(consent_document_path.is_relative_to(consents_dir))
                self.assertFalse((job_dir / "output").exists())
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )

    def test_get_consent_confirm_confirms_and_dispatches_without_extra_button(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"
            app = FastAPI()
            app.include_router(create_consent_router())
            client = TestClient(app)
            dispatched_records = []

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
                patch(
                    "src.web.consent_router._dispatch_materials_after_consent",
                    side_effect=lambda record: dispatched_records.append(record),
                ),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    sender_email="sender@example.com",
                )

                request_response = client.get(f"/consent/request/{record['token']}")
                self.assertEqual(request_response.status_code, 200)
                self.assertNotIn('method="post"', request_response.text)
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )

                confirm_response = client.get(f"/consent/confirm/{record['token']}")
                self.assertEqual(confirm_response.status_code, 200)
                self.assertNotIn('method="post"', confirm_response.text)
                self.assertNotIn("Подтвердить и получить материалы", confirm_response.text)
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )
                self.assertEqual(len(dispatched_records), 1)
                self.assertEqual(len(list(consents_dir.rglob("*.docx"))), 1)

    def test_materials_dispatch_after_consent_preserves_sender_state(self) -> None:
        calls: list[dict] = []
        record = {
            "job_id": "job-1",
            "row_id": "42",
            "recipient": "user@example.com",
            "transport": "rusender",
            "attachment_mode": "kp",
            "work_type": "stp_mo",
            "recipient_strategy": "primary_then_fallback",
            "sender_email": "sender@example.com",
            "campaign_name": "main campaign",
            "connection_id": "connection-1",
            "owner_username": "admin",
        }

        def fake_run_sender(**kwargs):
            calls.append(kwargs)
            return {
                "summary_text": "materials sent",
                "sent_rows": 1,
                "error_rows": 0,
                "rows": [{"id": "42", "recipient": "user@example.com", "result": "sent"}],
            }

        with patch("src.generator.delivery.sender_agent.run_sender", side_effect=fake_run_sender), patch.object(
            consent_router, "mark_materials_dispatch_result"
        ) as mark_result:
            consent_router._dispatch_materials_after_consent(record)

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["preserve_sender_state"])
        self.assertEqual(calls[0]["target_recipient"], "user@example.com")
        self.assertEqual(calls[0]["smtp_mailbox_id"], "connection-1")
        self.assertEqual(calls[0]["owner_username"], "admin")
        mark_result.assert_called_once()

    def test_materials_dispatch_recovers_connection_for_legacy_consent_record(self) -> None:
        calls: list[dict] = []
        record = {
            "job_id": "job-legacy",
            "row_id": "7",
            "recipient": "legacy@example.com",
            "transport": "smtp",
            "attachment_mode": "kp",
        }

        def fake_run_sender(**kwargs):
            calls.append(kwargs)
            return {
                "summary_text": "materials sent",
                "sent_rows": 1,
                "error_rows": 0,
                "rows": [{"id": "7", "recipient": "legacy@example.com", "result": "sent"}],
            }

        with (
            patch("src.generator.delivery.sender_agent.run_sender", side_effect=fake_run_sender),
            patch.object(
                consent_router,
                "_campaign_delivery_settings",
                return_value=("legacy-connection", "legacy-owner", "mailopost"),
            ),
            patch.object(consent_router, "mark_materials_dispatch_result") as mark_result,
        ):
            consent_router._dispatch_materials_after_consent(record)

        self.assertEqual(calls[0]["smtp_mailbox_id"], "legacy-connection")
        self.assertEqual(calls[0]["owner_username"], "legacy-owner")
        self.assertEqual(calls[0]["transport"], "mailopost")
        mark_result.assert_called_once()

    def test_post_consent_confirms_once_and_repeated_post_does_not_dispatch_again(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"
            app = FastAPI()
            app.include_router(create_consent_router())
            client = TestClient(app)
            dispatched_records = []

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
                patch(
                    "src.web.consent_router._dispatch_materials_after_consent",
                    side_effect=lambda record: dispatched_records.append(record),
                ),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    sender_email="sender@example.com",
                )

                first_response = client.post(f"/consent/confirm/{record['token']}")
                self.assertEqual(first_response.status_code, 200)
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )
                self.assertEqual(len(dispatched_records), 1)
                first_stored = consent_store.get_consent_by_token(record["token"])
                self.assertTrue(first_stored["materials_dispatch_requested_at"])
                first_dispatch_requested_at = first_stored["materials_dispatch_requested_at"]

                second_response = client.post(f"/consent/confirm/{record['token']}")
                self.assertEqual(second_response.status_code, 200)
                self.assertEqual(len(dispatched_records), 1)
                second_stored = consent_store.get_consent_by_token(record["token"])
                self.assertEqual(second_stored["materials_dispatch_requested_at"], first_dispatch_requested_at)
                self.assertEqual(len(list(consents_dir.rglob("*.docx"))), 1)

    def test_confirmed_consent_retries_materials_after_error(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    sender_email="sender@example.com",
                )

                first = consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")
                self.assertTrue(first["_dispatch_materials"])
                consent_store.mark_materials_dispatch_result(
                    job_id="job-1",
                    row_id=42,
                    recipient="user@example.com",
                    sent=False,
                    error="SMTP временно недоступен",
                    summary="Материалы не отправлены.",
                    attachment_mode="kp",
                )

                second = consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")
                stored = consent_store.get_consent_by_token(record["token"])

                self.assertTrue(second["_dispatch_materials"])
                self.assertEqual(stored["materials_status"], "queued")
                self.assertEqual(stored["materials_error"], "")
                self.assertEqual(stored["materials_dispatch_attempts"], 2)

    def test_concurrent_consent_confirmation_dispatches_once(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    sender_email="sender@example.com",
                )

                original_save_document = consent_store._save_consent_document
                first_save_started = threading.Event()
                release_first_save = threading.Event()
                save_count = 0
                save_count_lock = threading.Lock()

                def slow_first_save(record_arg: dict, *, job_id: str | None) -> Path:
                    nonlocal save_count
                    with save_count_lock:
                        save_count += 1
                        current_save = save_count
                    if current_save == 1:
                        first_save_started.set()
                        self.assertTrue(release_first_save.wait(timeout=2))
                    return original_save_document(record_arg, job_id=job_id)

                with patch.object(consent_store, "_save_consent_document", side_effect=slow_first_save):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        first = executor.submit(
                            consent_store.confirm_consent,
                            record["token"],
                            ip="127.0.0.1",
                            user_agent="first",
                        )
                        self.assertTrue(first_save_started.wait(timeout=2))
                        second = executor.submit(
                            consent_store.confirm_consent,
                            record["token"],
                            ip="127.0.0.2",
                            user_agent="second",
                        )
                        release_first_save.set()
                        results = [first.result(timeout=5), second.result(timeout=5)]

                self.assertEqual(sum(1 for item in results if item and item["_dispatch_materials"]), 1)
                stored = consent_store.get_consent_by_token(record["token"])
                self.assertEqual(stored["status"], "confirmed")
                self.assertTrue(stored["materials_dispatch_requested_at"])
                self.assertEqual(len(list(consents_dir.rglob("*.docx"))), 1)

    def test_confirmed_consent_is_scoped_to_attachment_mode(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                kp_request = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    attachment_mode="kp",
                )
                consent_store.confirm_consent(kp_request["token"], ip="127.0.0.1", user_agent="test")

                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                        attachment_mode="kp",
                    )
                )
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                        attachment_mode="both",
                    )
                )

                both_request = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    attachment_mode="both",
                )
                self.assertNotEqual(kp_request["token"], both_request["token"])
                consent_store.mark_consent_request_sent(
                    job_id="job-1",
                    row_id=42,
                    recipient="user@example.com",
                    provider={"message_id": "both-request"},
                    attachment_mode="both",
                )

                kp_stored = consent_store.get_consent_by_token(kp_request["token"])
                both_stored = consent_store.get_consent_by_token(both_request["token"])
                self.assertEqual(kp_stored["status"], "confirmed")
                self.assertNotIn("provider", kp_stored)
                self.assertEqual(both_stored["status"], "request_sent")
                self.assertEqual(both_stored["provider"], {"message_id": "both-request"})

                consent_store.confirm_consent(both_request["token"], ip="127.0.0.1", user_agent="test")
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                        attachment_mode="both",
                    )
                )
                self.assertTrue(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                        attachment_mode="contract",
                    )
                )
    def test_mark_request_sent_does_not_downgrade_confirmed_consent(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                    sender_email="sender@example.com",
                )
                consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")
                consent_store.mark_consent_request_sent(
                    job_id="job-1",
                    row_id=42,
                    recipient="user@example.com",
                    provider={"message_id": "request-message"},
                )

                stored = consent_store.get_consent_by_token(record["token"])
                self.assertEqual(stored["status"], "confirmed")
                self.assertEqual(stored["provider"], {"message_id": "request-message"})

    def test_collect_pending_materials_dispatches_finds_queued_and_retryable_errors(self) -> None:
        records = [
            {
                "status": "confirmed",
                "materials_status": "queued",
                "job_id": "job-1",
                "row_id": "1",
                "recipient": "one@example.com",
                "attachment_mode": "kp",
            },
            {
                "status": "confirmed",
                "materials_status": "sent",
                "job_id": "job-1",
                "row_id": "2",
                "recipient": "two@example.com",
                "attachment_mode": "kp",
            },
            {
                "status": "request_sent",
                "materials_status": "queued",
                "job_id": "job-1",
                "row_id": "3",
                "recipient": "three@example.com",
                "attachment_mode": "kp",
            },
            {
                "status": "confirmed",
                "materials_status": "error",
                "materials_error": "provider error",
                "materials_dispatch_completed_at": "2000-01-01T00:00:00",
                "job_id": "job-1",
                "row_id": "4",
                "recipient": "four@example.com",
                "attachment_mode": "kp",
            },
        ]

        with (
            patch.object(consent_router, "_iter_consent_job_ids", return_value=["job-1"]),
            patch.object(consent_router, "load_consent_records", return_value=records),
        ):
            pending = consent_router.collect_pending_materials_dispatches(limit=10)

        self.assertEqual([record["row_id"] for record in pending], ["1", "4"])

    def test_recover_pending_materials_dispatches_uses_recovery_dispatch(self) -> None:
        records = [
            {
                "status": "confirmed",
                "materials_status": "queued",
                "job_id": "job-1",
                "row_id": "1",
                "recipient": "one@example.com",
                "attachment_mode": "kp",
            },
            {
                "status": "confirmed",
                "materials_status": "sent",
                "job_id": "job-1",
                "row_id": "2",
                "recipient": "two@example.com",
                "attachment_mode": "kp",
            },
        ]
        calls: list[str] = []

        def fake_dispatch(record: dict, **_: object) -> dict:
            calls.append(record["row_id"])
            return {"status": "completed", "sent_rows": 1, "error_rows": 0, "rows": [{"result": "sent"}]}

        with (
            patch.object(consent_router, "_iter_consent_job_ids", return_value=["job-1"]),
            patch.object(consent_router, "load_consent_records", return_value=records),
            patch.object(consent_router, "_dispatch_materials_after_consent", side_effect=fake_dispatch),
        ):
            result = consent_router.recover_pending_materials_dispatches(limit=10)

        self.assertEqual(calls, ["1"])
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 0)

    def test_materials_recovery_stops_after_max_attempts(self) -> None:
        records = [
            {
                "status": "confirmed",
                "materials_status": "error",
                "materials_error": "receiver does not exist",
                "materials_dispatch_attempts": 3,
                "materials_dispatch_completed_at": "2000-01-01T00:00:00",
                "job_id": "job-1",
                "row_id": "1",
                "recipient": "bad@example.com",
                "attachment_mode": "kp",
            }
        ]

        with (
            patch.object(consent_router, "_iter_consent_job_ids", return_value=["job-1"]),
            patch.object(consent_router, "load_consent_records", return_value=records),
            patch.object(consent_router.settings, "consent_materials_recovery_max_attempts", 3),
        ):
            pending = consent_router.collect_pending_materials_dispatches(limit=10)

        self.assertEqual(pending, [])

if __name__ == "__main__":
    unittest.main()
