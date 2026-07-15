from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.generation.document_builder import DOCUMENT_RENDERER_VERSION

from src.security.auth import Principal
from src.web.documents_router import create_documents_router
from src.web.generator_router import create_generator_router
from src.web.philologist_router import create_philologist_router
from src.web.sender_router import create_sender_router


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _workspace_temp_root(prefix: str) -> Path:
    root = PROJECT_ROOT / "tmp" / f"{prefix}-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


class ApiErrorContractTests(unittest.TestCase):
    def _sender_client(self, *, logs: list[tuple], failure: RuntimeError) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_sender_router(
                check_auth=lambda: Principal("admin", "root", "admin"),
                parse_optional_limit=lambda payload: None,
                compact_sender_status=lambda state: state,
                clear_sender_stop_request=lambda job_id: (_ for _ in ()).throw(failure),
                prime_sender_checking_state=lambda *args, **kwargs: {},
                prime_sender_running_state=lambda *args, **kwargs: {},
                prime_sender_queued_state=lambda *args, **kwargs: {},
                prime_sender_scheduled_state=lambda *args, **kwargs: {},
                start_sender_thread_if_absent=lambda *args, **kwargs: (
                    {"task_id": "task-1", "created": True, "queue_position": 1, "queue_total": 1},
                    True,
                ),
                run_sender_background=lambda **kwargs: None,
                sender_job_key=lambda job_id: job_id or "default",
                get_sender_status=lambda job_id: {},
                get_generator_status=lambda job_id: {},
                get_unisender_history=lambda **kwargs: {},
                build_sender_delivery_analytics=lambda **kwargs: {},
                settings=SimpleNamespace(
                    unisender_webhook_token="secret-unisender",
                    unisender_webhook_secret="",
                    rusender_webhook_token="secret-rusender",
                    rusender_webhook_secret="",
                    webhook_max_body_bytes=2048,
                ),
                append_unisender_go_events=lambda payload: (_ for _ in ()).throw(failure),
                append_rusender_events=lambda payload: (_ for _ in ()).throw(failure),
                append_mailopost_events=lambda payload: (_ for _ in ()).throw(failure),
                logger=SimpleNamespace(exception=lambda *args, **kwargs: logs.append((args, kwargs))),
                request_sender_stop=lambda **kwargs: {},
                preview_recipients=lambda **kwargs: {},
                chat_with_sender=lambda message, job_id=None, session_id=None: {"reply": message},
                is_load_test_job=lambda job_id: False,
            )
        )
        return TestClient(app)

    def test_sender_run_500_hides_internal_exception_detail(self) -> None:
        logs: list[tuple] = []
        client = self._sender_client(logs=logs, failure=RuntimeError("smtp password leaked"))

        response = client.post("/api/sender/run", json={"attachment_mode": "kp"})

        payload = response.json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["detail"], "Не удалось запустить отправку.")
        self.assertNotIn("RuntimeError", payload["detail"])
        self.assertNotIn("smtp password leaked", payload["detail"])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0][0], "sender_run_start_failed")

    def test_unisender_webhook_500_hides_internal_exception_detail(self) -> None:
        logs: list[tuple] = []
        client = self._sender_client(logs=logs, failure=RuntimeError("provider api secret"))

        response = client.post("/api/webhooks/unisender-go/secret-unisender", json={"event": "delivered"})

        payload = response.json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["detail"], "Не удалось сохранить webhook UniSender Go.")
        self.assertNotIn("RuntimeError", payload["detail"])
        self.assertNotIn("provider api secret", payload["detail"])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0][0], "unisender_go_webhook_save_failed")

    def test_rusender_webhook_500_hides_internal_exception_detail(self) -> None:
        logs: list[tuple] = []
        client = self._sender_client(logs=logs, failure=RuntimeError("provider api secret"))

        response = client.post("/api/webhooks/rusender/secret-rusender", json={"event": "external_mail.delivered"})

        payload = response.json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["detail"], "Не удалось сохранить webhook RuSender.")
        self.assertNotIn("RuntimeError", payload["detail"])
        self.assertNotIn("provider api secret", payload["detail"])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0][0], "rusender_webhook_save_failed")

    def test_documents_start_409_when_successful_generation_is_locked(self) -> None:
        root = _workspace_temp_root("api-error-documents-locked")
        try:
            xlsx_path = root / "data.xlsx"
            xlsx_path.write_bytes(b"xlsx")
            starts: list[tuple] = []
            app = FastAPI()
            app.include_router(
                create_documents_router(
                    check_auth=lambda: Principal("admin", "root", "admin"),
                    prefer_existing_file=lambda primary, fallback: xlsx_path,
                    compact_documents_status=lambda job_id, document_mode=None: {
                        "status": "completed",
                        "restart_locked": True,
                    },
                    get_generator_thread=lambda job_id: None,
                    get_philologist_thread=lambda job_id: None,
                    prime_philologist_running_state=lambda job_id, mode: {},
                    start_documents_thread_if_absent=lambda *args, **kwargs: starts.append((args, kwargs)),
                    run_documents_pipeline_background=lambda **kwargs: None,
                    documents_job_key=lambda job_id: job_id or "default",
                    clear_philologist_stop_request=lambda job_id: None,
                    get_generator_status=lambda job_id: {
                        "status": "completed",
                        "document_mode": "kp",
                        "work_type": "mngp_settlements",
                        "renderer_version": DOCUMENT_RENDERER_VERSION,
                        "error_rows": 0,
                        "output_file_count": 2,
                    },
                    get_philologist_status=lambda job_id: {"status": "completed"},
                    clear_generator_stop_request=lambda job_id: None,
                    save_generator_state=lambda state, job_id: None,
                    prime_generator_state=lambda **kwargs: {},
                    request_generator_stop=lambda job_id: {},
                    request_philologist_stop=lambda job_id: {},
                    documents_agent_choose_reply=lambda message, job_id=None, session_id=None: {"reply": message},
                )
            )

            response = TestClient(app).post(
                "/api/documents/start",
                json={"job_id": "job-test", "document_mode": "kp", "work_type": "mngp_settlements"},
            )

            payload = response.json()
            self.assertEqual(response.status_code, 409)
            self.assertIn("Повторный запуск", payload["detail"])
            self.assertEqual(starts, [])
        finally:
            _cleanup(root)
    def test_documents_start_500_hides_internal_exception_detail(self) -> None:
        root = _workspace_temp_root("api-error-documents")
        try:
            xlsx_path = root / "data.xlsx"
            xlsx_path.write_bytes(b"xlsx")
            logs: list[tuple] = []
            app = FastAPI()
            app.include_router(
                create_documents_router(
                    check_auth=lambda: Principal("admin", "root", "admin"),
                    prefer_existing_file=lambda primary, fallback: xlsx_path,
                    compact_documents_status=lambda job_id, document_mode=None: (_ for _ in ()).throw(
                        RuntimeError("state backend password leaked")
                    ),
                    get_generator_thread=lambda job_id: None,
                    get_philologist_thread=lambda job_id: None,
                    prime_philologist_running_state=lambda job_id, mode: {},
                    start_documents_thread_if_absent=lambda *args, **kwargs: (None, True),
                    run_documents_pipeline_background=lambda **kwargs: None,
                    documents_job_key=lambda job_id: job_id or "default",
                    clear_philologist_stop_request=lambda job_id: None,
                    get_generator_status=lambda job_id: {},
                    get_philologist_status=lambda job_id: {},
                    clear_generator_stop_request=lambda job_id: None,
                    save_generator_state=lambda state, job_id: None,
                    prime_generator_state=lambda **kwargs: {},
                    request_generator_stop=lambda job_id: {},
                    request_philologist_stop=lambda job_id: {},
                    documents_agent_choose_reply=lambda message, job_id=None, session_id=None: {"reply": message},
                )
            )

            with patch("src.web.documents_router.logger", SimpleNamespace(exception=lambda *args, **kwargs: logs.append((args, kwargs)))):
                response = TestClient(app).post(
                    "/api/documents/start",
                    json={"job_id": "job-test", "document_mode": "kp"},
                )

            payload = response.json()
            self.assertEqual(response.status_code, 500)
            self.assertEqual(payload["detail"], "Не удалось прочитать состояние подготовки.")
            self.assertNotIn("RuntimeError", payload["detail"])
            self.assertNotIn("state backend password leaked", payload["detail"])
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0][0][0], "documents_start_state_read_failed")
        finally:
            _cleanup(root)


class WorkerLimitContractTests(unittest.TestCase):
    def test_generator_limit_is_checked_before_state_is_primed(self) -> None:
        root = _workspace_temp_root("api-error-generator-limit")
        try:
            xlsx_path = root / "data.xlsx"
            xlsx_path.write_bytes(b"xlsx")
            prime_calls: list[dict] = []
            started: list[str] = []

            async def job_readiness(**kwargs) -> dict:
                return {"status": "ok", "result": {"counts": {}}}

            app = FastAPI()
            app.include_router(
                create_generator_router(
                    check_auth=lambda: Principal("admin", "root", "admin"),
                    job_readiness=job_readiness,
                    prefer_existing_file=lambda primary, fallback: primary,
                    resolve_job_paths=lambda job_id=None: SimpleNamespace(data_xlsx=xlsx_path),
                    get_generator_thread=lambda job_id: None,
                    compact_generator_status=lambda state: state,
                    get_generator_status=lambda job_id: {},
                    clear_generator_stop_request=lambda job_id: None,
                    prime_generator_state=lambda **kwargs: prime_calls.append(kwargs) or {"status": "running"},
                    schedule_output_archive_build=lambda job_id: None,
                    run_generator_background=lambda **kwargs: started.append("run"),
                    generator_job_key=lambda job_id: job_id or "default",
                    register_generator_thread=lambda job_id, thread: started.append("register"),
                    request_generator_stop=lambda job_id: {},
                    ensure_user_inprocess_limit=lambda job_id: (_ for _ in ()).throw(RuntimeError("limit reached")),
                )
            )

            response = TestClient(app).post("/api/generate", json={"job_id": "job-a"})

            self.assertEqual(response.status_code, 400)
            self.assertEqual(prime_calls, [])
            self.assertEqual(started, [])
        finally:
            _cleanup(root)

    def test_philologist_limit_is_checked_before_state_is_primed(self) -> None:
        prime_calls: list[tuple] = []
        started: list[str] = []
        app = FastAPI()
        app.include_router(
            create_philologist_router(
                check_auth=lambda: Principal("admin", "root", "admin"),
                get_philologist_thread=lambda job_id: None,
                compact_philologist_status=lambda state: state,
                get_philologist_status=lambda job_id: {},
                clear_philologist_stop_request=lambda job_id: started.append("clear"),
                prime_philologist_running_state=lambda job_id, mode: prime_calls.append((job_id, mode)) or {"status": "running"},
                run_philologist_background=lambda **kwargs: started.append("run"),
                philologist_job_key=lambda job_id: job_id or "default",
                register_philologist_thread=lambda job_id, thread: started.append("register"),
                request_philologist_stop=lambda job_id: {},
                build_philologist_plan=lambda job_id: {},
                chat_with_philologist=lambda message, job_id=None: {"reply": message},
                ensure_user_inprocess_limit=lambda job_id: (_ for _ in ()).throw(RuntimeError("limit reached")),
            )
        )

        response = TestClient(app).post("/api/philologist/run", json={"job_id": "job-a"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(prime_calls, [])
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
