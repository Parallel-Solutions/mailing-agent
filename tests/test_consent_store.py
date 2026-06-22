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
from src.web.consent_router import create_consent_router


@contextmanager
def _workspace_temp_dir() -> Iterator[Path]:
    root = Path(__file__).resolve().parents[1] / f"test-consent-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ConsentStoreTests(unittest.TestCase):
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
                )

                self.assertIn("/consent/confirm/", record["consent_url"])
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

    def test_get_consent_routes_render_form_without_confirming(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = Path(tmpdir) / "job-1" / "state" / "consents.json"
            job_dir = consent_path.parent.parent
            consents_dir = job_dir / "consents"
            app = FastAPI()
            app.include_router(create_consent_router())
            client = TestClient(app)

            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
                patch("src.web.consent_router._dispatch_materials_after_consent") as dispatch_materials,
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-1",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="user@example.com",
                    transport="smtp",
                )

                for path in (f"/consent/confirm/{record['token']}", f"/consent/request/{record['token']}"):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('method="post"', response.text)
                    self.assertIn(f'action="/consent/confirm/{record["token"]}"', response.text)

                dispatch_materials.assert_not_called()
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-1",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )
                self.assertEqual(list(consents_dir.rglob("*.docx")), [])

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


if __name__ == "__main__":
    unittest.main()
