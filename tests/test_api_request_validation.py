from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security.auth import Principal
from src.web.sender_router import create_sender_router
from src.web.workers_router import create_workers_router


class SenderRequestValidationTests(unittest.TestCase):
    def _client(self):
        calls: list[dict] = []

        def check_auth() -> Principal:
            return Principal(username="admin", tenant_id="tenant-a", role="admin")

        def start_sender_thread_if_absent(job_id, **kwargs):
            before_start = kwargs.get("before_start")
            if before_start:
                before_start()
            calls.append({"job_id": job_id, **kwargs})
            return object(), True

        app = FastAPI()
        logger = SimpleNamespace(exception=lambda *args, **kwargs: None)
        app.include_router(
            create_sender_router(
                check_auth=check_auth,
                parse_optional_limit=lambda payload: None,
                compact_sender_status=lambda state: dict(state),
                clear_sender_stop_request=lambda job_id: None,
                prime_sender_checking_state=lambda job_id, transport, attachment_mode, recipient_strategy=None, sender_email=None: {
                    "status": "running",
                    "mode": "dry_run",
                    "transport": transport,
                    "attachment_mode": attachment_mode,
                },
                prime_sender_running_state=lambda job_id, transport, attachment_mode, recipient_strategy=None, sender_email=None: {
                    "status": "running",
                    "mode": "send",
                    "transport": transport,
                    "attachment_mode": attachment_mode,
                },
                start_sender_thread_if_absent=start_sender_thread_if_absent,
                run_sender_background=lambda **kwargs: None,
                sender_job_key=lambda job_id: str(job_id or "__legacy__"),
                get_sender_status=lambda job_id: {"status": "idle"},
                get_generator_status=lambda job_id: {"document_mode": "", "work_type": "default-work"},
                get_unisender_history=lambda **kwargs: {},
                build_sender_delivery_analytics=lambda **kwargs: {},
                settings=SimpleNamespace(webhook_max_body_bytes=1024),
                append_unisender_go_events=lambda payload: {},
                append_rusender_events=lambda payload: {},
                append_mailopost_events=lambda payload: {},
                logger=logger,
                request_sender_stop=lambda **kwargs: {"status": "stopped"},
                preview_recipients=lambda **kwargs: {"items": []},
                chat_with_sender=lambda message, job_id=None: {"reply": message},
                is_load_test_job=lambda job_id: False,
            )
        )
        return TestClient(app), calls

    def test_sender_run_rejects_invalid_limit_before_starting_worker(self) -> None:
        client, calls = self._client()

        response = client.post("/api/sender/run", json={"job_id": "job-api", "limit": "not-a-number"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_sender_run_treats_empty_or_zero_limit_as_unlimited_for_ui_payload(self) -> None:
        for raw_limit in (None, 0, "0"):
            with self.subTest(raw_limit=raw_limit):
                client, calls = self._client()

                with patch("src.web.sender_router.append_audit_event", lambda **kwargs: None):
                    response = client.post(
                        "/api/sender/run",
                        json={"job_id": "job-api", "dry_run": True, "limit": raw_limit},
                    )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(calls), 1)
                self.assertIsNone(calls[0]["kwargs"]["limit"])

    def test_sender_run_accepts_existing_ui_payload_names(self) -> None:
        client, calls = self._client()
        payload = {
            "job_id": "job-api",
            "dry_run": True,
            "limit": 2,
            "transport": "unisender",
            "send_mode": "materials",
            "attachment_mode": "contract",
            "mail_subject": "  Subject  ",
            "sender_email": "  sender@example.com  ",
            "work_type": "  custom-work  ",
        }

        with patch("src.web.sender_router.append_audit_event", lambda **kwargs: None):
            response = client.post("/api/sender/run", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        worker_kwargs = calls[0]["kwargs"]
        self.assertEqual(worker_kwargs["limit"], 2)
        self.assertEqual(worker_kwargs["transport"], "unisender")
        self.assertEqual(worker_kwargs["send_mode"], "materials")
        self.assertEqual(worker_kwargs["attachment_mode"], "contract")
        self.assertEqual(worker_kwargs["subject_template"], "Subject")
        self.assertEqual(worker_kwargs["sender_email"], "sender@example.com")
        self.assertEqual(worker_kwargs["work_type"], "custom-work")


class WorkerRequestValidationTests(unittest.TestCase):
    WORKER_JOBS_DIR = Path.cwd() / "tmp" / "api-request-validation" / "jobs"

    def _client(self, jobs_dir: Path):
        calls: list[dict] = []

        def check_auth() -> Principal:
            return Principal(username="admin", tenant_id="tenant-a", role="admin")

        app = FastAPI()
        app.include_router(
            create_workers_router(
                check_auth=check_auth,
                jobs_dir=jobs_dir,
                list_worker_statuses=lambda *args, **kwargs: [],
                terminate_worker_process=lambda **kwargs: calls.append(kwargs) or {"terminated": True},
            )
        )
        return TestClient(app), calls

    def test_worker_stop_requires_status_path_model_field(self) -> None:
        client, calls = self._client(self.WORKER_JOBS_DIR)

        response = client.post("/api/workers/stop", json={"pid": 4321})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_worker_stop_rejects_invalid_pid_before_termination(self) -> None:
        jobs_dir = self.WORKER_JOBS_DIR
        status_path = jobs_dir / "job-api" / "state" / "worker-sender-abc.status.json"
        client, calls = self._client(jobs_dir)

        response = client.post("/api/workers/stop", json={"status_path": str(status_path), "pid": "not-a-pid"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_worker_stop_accepts_normalized_status_path_and_pid(self) -> None:
        jobs_dir = self.WORKER_JOBS_DIR
        status_path = jobs_dir / "job-api" / "state" / "worker-sender-abc.status.json"
        client, calls = self._client(jobs_dir)

        with patch("src.web.workers_router.append_audit_event", lambda **kwargs: None):
            response = client.post("/api/workers/stop", json={"status_path": str(status_path), "pid": 4321})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [{"status_path": str(status_path.resolve(strict=False)), "pid": 4321}])


if __name__ == "__main__":
    unittest.main()
