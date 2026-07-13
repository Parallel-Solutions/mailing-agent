from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security.auth import Principal
from src.web.generator_router import create_generator_router
from src.web.jobs_router import JobsWebController
from src.web.sender_router import create_sender_router


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _workspace_temp_root(prefix: str) -> Path:
    root = PROJECT_ROOT / "tmp" / f"{prefix}-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _job_paths(root: Path, job_id: str | None) -> SimpleNamespace:
    base = root / ("legacy" if not job_id else f"jobs/{job_id}")
    return SimpleNamespace(
        job_id=job_id,
        root_dir=base,
        data_xlsx=base / "input" / "data.xlsx",
        base_xlsx=base / "input" / "base.xlsx",
        templates_dir=base / "templates",
        output_dir=base / "output",
        consents_dir=base / "consents",
        sent_mail_log_path=base / "sent_mail_log.jsonl",
        uses_legacy_layout=job_id is None,
        ensure_dirs=lambda: base.mkdir(parents=True, exist_ok=True),
    )


def _jobs_controller(root: Path, *, row_count: int = 0, parser_status: dict | None = None) -> JobsWebController:
    resolver = lambda job_id=None: _job_paths(root, job_id)
    return JobsWebController(
        check_auth=lambda: Principal("admin", "root", "admin"),
        settings=SimpleNamespace(upload_data_max_bytes=1024, upload_template_max_bytes=1024),
        logger=SimpleNamespace(exception=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
        prefer_existing_file=lambda primary, fallback: primary if primary.exists() else fallback,
        validate_uploaded_file=lambda *args, **kwargs: "file.xlsx",
        cached_excel_row_count=lambda path: row_count,
        cached_tree_file_count=lambda path, pattern: 0,
        safe_int=lambda value: int(value or 0),
        create_job_id=lambda: "job-contract",
        resolve_job_paths=resolver,
        jobs_dir=root / "jobs",
        create_documents_load_test_job=lambda **kwargs: {},
        start_parser_verification_process=lambda **kwargs: None,
        get_parser_status=lambda job_id: parser_status or {},
        get_generator_status=lambda job_id: {},
        get_philologist_status=lambda job_id, include_details=False: {},
        get_sender_status=lambda job_id: {},
        run_parser_municipality_verification=lambda *args, **kwargs: {},
    )


class JobsResponseContractTests(unittest.TestCase):
    def test_create_job_returns_result_and_legacy_job_id(self) -> None:
        root = _workspace_temp_root("api-response-jobs")
        try:
            controller = _jobs_controller(root)
            app = FastAPI()
            app.include_router(controller.router)

            with (
                patch("src.jobs.access.resolve_job_paths", side_effect=lambda job_id=None: _job_paths(root, job_id)),
                patch("src.web.jobs_router.append_audit_event", lambda **kwargs: None),
            ):
                response = TestClient(app).post("/api/jobs")

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["job_id"], "job-contract")
            self.assertEqual(payload["result"], {"job_id": "job-contract"})
        finally:
            _cleanup(root)

    def test_data_info_returns_result_and_legacy_fields(self) -> None:
        root = _workspace_temp_root("api-response-data-info")
        try:
            paths = _job_paths(root, None)
            paths.data_xlsx.parent.mkdir(parents=True)
            paths.data_xlsx.write_bytes(b"xlsx")
            controller = _jobs_controller(root, row_count=7)
            app = FastAPI()
            app.include_router(controller.router)

            response = TestClient(app).get("/api/data/info")

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["loaded"], True)
            self.assertEqual(payload["total"], 7)
            self.assertEqual(payload["result"], {"loaded": True, "total": 7})
        finally:
            _cleanup(root)

    def test_job_readiness_honors_document_mode_query(self) -> None:
        root = _workspace_temp_root("api-response-readiness-mode")
        try:
            job_id = "job-kp-mode"
            paths = _job_paths(root, job_id)
            paths.data_xlsx.parent.mkdir(parents=True)
            paths.data_xlsx.write_bytes(b"xlsx")
            paths.templates_dir.mkdir(parents=True)
            (paths.templates_dir / "kp_template_source.docx").write_bytes(b"docx")
            controller = _jobs_controller(
                root,
                row_count=1,
                parser_status={"municipality_name_verification_state": {"status": "completed"}},
            )
            app = FastAPI()
            app.include_router(controller.router)

            with patch("src.jobs.access.resolve_job_paths", side_effect=lambda job_id=None: _job_paths(root, job_id)):
                response = TestClient(app).get(f"/api/job/readiness?job_id={job_id}&document_mode=kp")

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["result"]["document_mode"], "kp")
            self.assertTrue(payload["result"]["generator_ready"])
            self.assertNotIn("договора", payload["result"]["generator_reason"])
        finally:
            _cleanup(root)

    def test_upload_data_returns_result_and_legacy_fields(self) -> None:
        root = _workspace_temp_root("api-response-upload-data")
        try:
            controller = _jobs_controller(root)
            app = FastAPI()
            app.include_router(controller.router)

            with (
                patch("src.jobs.access.resolve_job_paths", side_effect=lambda job_id=None: _job_paths(root, job_id)),
                patch("src.web.jobs_router.append_audit_event", lambda **kwargs: None),
                patch("src.jobs.clients_store.import_clients_from_xlsx", return_value=1),
            ):
                response = TestClient(app).post(
                    "/api/upload/data",
                    data={"job_id": "job-upload", "upload_token": "token-1"},
                    files={
                        "file": (
                            "data.xlsx",
                            b"xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["job_id"], "job-upload")
            self.assertEqual(payload["filename"], "file.xlsx")
            self.assertEqual(payload["result"]["job_id"], "job-upload")
            self.assertEqual(payload["result"]["filename"], "file.xlsx")
            self.assertTrue(payload["result"]["verification_background"])
        finally:
            _cleanup(root)

    def test_upload_template_returns_result_and_legacy_fields(self) -> None:
        root = _workspace_temp_root("api-response-upload-template")
        try:
            controller = _jobs_controller(root)
            app = FastAPI()
            app.include_router(controller.router)

            with (
                patch("src.jobs.access.resolve_job_paths", side_effect=lambda job_id=None: _job_paths(root, job_id)),
                patch("src.web.jobs_router.append_audit_event", lambda **kwargs: None),
                patch("src.generator.generation.config_generator.KP_ADAPTIVE_TEMPLATE_ENGINE", False),
            ):
                response = TestClient(app).post(
                    "/api/upload/template",
                    data={"job_id": "job-template", "template_kind": "kp"},
                    files={
                        "file": (
                            "kp.docx",
                            b"docx",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    },
                )

            payload = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["job_id"], "job-template")
            self.assertEqual(payload["stored_as"], "kp_template_source.docx")
            self.assertEqual(payload["result"]["job_id"], "job-template")
            self.assertEqual(payload["result"]["stored_as"], "kp_template_source.docx")
        finally:
            _cleanup(root)


class GeneratorResponseContractTests(unittest.TestCase):
    def test_counts_returns_result_and_legacy_top_level_counts(self) -> None:
        async def job_readiness(**kwargs) -> dict:
            return {
                "status": "ok",
                "result": {
                    "counts": {
                        "parser_total": 1,
                        "generator_total": 2,
                        "sender_total": 3,
                    }
                },
            }

        app = FastAPI()
        app.include_router(
            create_generator_router(
                check_auth=lambda: Principal("admin", "root", "admin"),
                job_readiness=job_readiness,
                prefer_existing_file=lambda primary, fallback: primary,
                resolve_job_paths=lambda job_id=None: SimpleNamespace(data_xlsx=Path("missing.xlsx")),
                get_generator_thread=lambda job_id: None,
                compact_generator_status=lambda state: state,
                get_generator_status=lambda job_id: {},
                clear_generator_stop_request=lambda job_id: None,
                prime_generator_state=lambda **kwargs: {},
                schedule_output_archive_build=lambda job_id: None,
                run_generator_background=lambda **kwargs: None,
                generator_job_key=lambda job_id: str(job_id or "__legacy__"),
                register_generator_thread=lambda job_id, thread: None,
                request_generator_stop=lambda job_id: {},
            )
        )

        response = TestClient(app).get("/api/counts")

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["parser_total"], 1)
        self.assertEqual(payload["generator_total"], 2)
        self.assertEqual(payload["sender_total"], 3)
        self.assertEqual(
            payload["result"],
            {"parser_total": 1, "generator_total": 2, "sender_total": 3},
        )


class SenderResponseContractTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_sender_router(
                check_auth=lambda: Principal("admin", "root", "admin"),
                parse_optional_limit=lambda payload: None,
                compact_sender_status=lambda state: state,
                clear_sender_stop_request=lambda job_id: None,
                prime_sender_checking_state=lambda *args, **kwargs: {},
                prime_sender_running_state=lambda *args, **kwargs: {},
                start_sender_thread_if_absent=lambda *args, **kwargs: (None, True),
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
                    mailopost_webhook_token="secret-mailopost",
                    mailopost_webhook_secret="",
                    webhook_max_body_bytes=2048,
                ),
                append_unisender_go_events=lambda payload: {"saved": 1},
                append_rusender_events=lambda payload: {"saved": 1},
                append_mailopost_events=lambda payload: {"saved": 1},
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
                request_sender_stop=lambda **kwargs: {},
                preview_recipients=lambda **kwargs: {},
                chat_with_sender=lambda message, job_id=None, session_id=None: {"reply": f"echo:{message}", "job_id": job_id},
                is_load_test_job=lambda job_id: False,
            )
        )
        return TestClient(app)

    def test_webhook_health_returns_result_and_legacy_fields(self) -> None:
        response = self._client().get("/api/webhooks/unisender-go")

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["message"], "UniSender Go webhook endpoint is ready")
        self.assertEqual(payload["result"]["message"], "UniSender Go webhook endpoint is ready")
        self.assertEqual(payload["result"]["max_body_bytes"], 2048)

    def test_sender_chat_returns_result_and_legacy_reply(self) -> None:
        response = self._client().post("/api/sender/chat", json={"message": "hello"})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["reply"], "echo:hello")
        self.assertEqual(payload["result"]["reply"], "echo:hello")


if __name__ == "__main__":
    unittest.main()
