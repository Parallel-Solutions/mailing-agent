from __future__ import annotations

import json
import shutil
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.delivery import consent_store
from src.jobs.access import JobAccessDenied, assign_job_owner, authorize_job_access
from src.jobs.audit import append_audit_event
from src.security.auth import Principal, authenticate_basic_user
from src.web.jobs_router import JobsWebController
from src.web.workers_router import create_workers_router


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _workspace_temp_dir(prefix: str = "test-multi-user") -> Iterator[Path]:
    root = PROJECT_ROOT / f"{prefix}-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _job_paths(root: Path, job_id: str | None) -> SimpleNamespace:
    if not job_id:
        return SimpleNamespace(
            job_id=None,
            root_dir=root / "legacy",
            data_xlsx=root / "legacy" / "data.xlsx",
            base_xlsx=root / "legacy" / "base.xlsx",
            templates_dir=root / "legacy" / "templates",
            output_dir=root / "legacy" / "output",
            consents_dir=root / "legacy" / "consents",
            sent_mail_log_path=root / "legacy" / "sent_mail_log.jsonl",
            uses_legacy_layout=True,
            ensure_dirs=lambda: None,
        )
    job_root = root / "jobs" / job_id
    return SimpleNamespace(
        job_id=job_id,
        root_dir=job_root,
        data_xlsx=job_root / "input" / "data.xlsx",
        base_xlsx=job_root / "input" / "base.xlsx",
        templates_dir=job_root / "templates",
        output_dir=job_root / "output",
        consents_dir=job_root / "consents",
        sent_mail_log_path=job_root / "sent_mail_log.jsonl",
        uses_legacy_layout=False,
        ensure_dirs=lambda: job_root.mkdir(parents=True, exist_ok=True),
    )


class MultiUserAccessTests(unittest.TestCase):
    def test_app_users_authenticate_to_tenant_principal_and_admin_fallback(self) -> None:
        settings = SimpleNamespace(
            app_username="admin",
            app_password="admin-pass",
            app_admin_tenant_id="root",
            app_users=json.dumps(
                {
                    "alice": {"password": "alice-pass", "tenant_id": "tenant-a", "role": "user"},
                    "bob": {"password": "bob-pass", "tenant_id": "tenant-a", "role": "user"},
                }
            ),
        )

        alice = authenticate_basic_user("alice", "alice-pass", settings)
        admin = authenticate_basic_user("admin", "admin-pass", settings)

        self.assertEqual(alice, Principal(username="alice", tenant_id="tenant-a", role="user"))
        self.assertEqual(admin, Principal(username="admin", tenant_id="root", role="admin"))
        self.assertIsNone(authenticate_basic_user("alice", "wrong", settings))

    def test_job_access_allows_owner_only_and_rejects_other_users(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            resolver = lambda job_id=None: _job_paths(tmpdir, job_id)
            with patch("src.jobs.access.resolve_job_paths", side_effect=resolver):
                paths = resolver("job-a")
                paths.ensure_dirs()
                assign_job_owner("job-a", Principal("alice", "tenant-a"))

                self.assertEqual(authorize_job_access("job-a", Principal("alice", "tenant-a")), "job-a")
                with self.assertRaises(JobAccessDenied):
                    authorize_job_access("job-a", Principal("bob", "tenant-a"))
                with self.assertRaises(JobAccessDenied):
                    authorize_job_access("job-a", Principal("mallory", "tenant-b"))
                self.assertEqual(authorize_job_access("job-a", Principal("admin", "root", "admin")), "job-a")

    def test_jobs_history_filters_to_current_owner(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            jobs_dir = tmpdir / "jobs"
            resolver = lambda job_id=None: _job_paths(tmpdir, job_id)
            for job_id, owner_username in (("job-a", "alice"), ("job-b", "bob")):
                job_root = jobs_dir / job_id
                (job_root / "state").mkdir(parents=True)
                (job_root / "state" / "sender.json").write_text(
                    json.dumps({"mode": "send", "status": "completed", "sent_rows": 1}),
                    encoding="utf-8",
                )
                (job_root / "input").mkdir(parents=True)
                (job_root / "input" / "data.xlsx").write_bytes(b"xlsx")
                with patch("src.jobs.access.resolve_job_paths", side_effect=resolver):
                    assign_job_owner(job_id, Principal(owner_username, "tenant-a"))

            controller = JobsWebController(
                check_auth=lambda: Principal("alice", "tenant-a"),
                settings=SimpleNamespace(upload_data_max_bytes=1024, upload_template_max_bytes=1024),
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
                prefer_existing_file=lambda primary, fallback: primary if primary.exists() else fallback,
                validate_uploaded_file=lambda *args, **kwargs: "file.xlsx",
                cached_excel_row_count=lambda path: 1,
                cached_tree_file_count=lambda path, pattern: 0,
                safe_int=lambda value: int(value or 0),
                create_job_id=lambda: "job-new",
                resolve_job_paths=resolver,
                jobs_dir=jobs_dir,
                create_documents_load_test_job=lambda **kwargs: {},
                start_parser_verification_process=lambda **kwargs: None,
                get_parser_status=lambda job_id: {},
                get_generator_status=lambda job_id: {},
                get_philologist_status=lambda job_id, include_details=False: {},
                get_sender_status=lambda job_id: {},
                run_parser_municipality_verification=lambda *args, **kwargs: {},
            )
            app = FastAPI()
            app.include_router(controller.router)

            with patch("src.jobs.access.resolve_job_paths", side_effect=resolver):
                response = TestClient(app).get("/api/jobs/history")

        self.assertEqual(response.status_code, 200)
        jobs = response.json()["result"]["jobs"]
        self.assertEqual([item["job_id"] for item in jobs], ["job-a"])

    def test_jobs_history_counts_main_campaign_from_sent_log_after_materials_followup(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            jobs_dir = tmpdir / "jobs"
            resolver = lambda job_id=None: _job_paths(tmpdir, job_id)
            job_id = "job-a"
            job_root = jobs_dir / job_id
            (job_root / "state").mkdir(parents=True)
            (job_root / "input").mkdir(parents=True)
            (job_root / "input" / "data.xlsx").write_bytes(b"xlsx")
            (job_root / "state" / "sender.json").write_text(
                json.dumps(
                    {
                        "mode": "send",
                        "status": "completed",
                        "send_mode": "materials",
                        "selection_scoped": True,
                        "sent_rows": 1,
                        "error_rows": 0,
                        "total_rows": 1,
                        "stats": {"total": 48, "sent": 1, "error": 0},
                        "campaign_name": "техническая доотправка",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            sent_items = [
                {
                    "sent_at": f"2026-07-02T15:{index:02d}:00",
                    "row_id": str(index),
                    "recipient": f"person{index}@example.com",
                    "send_mode": "consent_request",
                    "campaign_name": "СТП_Регионы на буквы Е,З,И_48",
                    "work_type": "stp_mo",
                }
                for index in range(1, 35)
            ]
            sent_items.append(
                {
                    "sent_at": "2026-07-03T06:42:30",
                    "row_id": "2",
                    "recipient": "37zavadm@ivreg.ru",
                    "send_mode": "materials",
                    "campaign_name": "СТП_Регионы на буквы Е,З,И_48",
                    "work_type": "stp_mo",
                }
            )
            (job_root / "sent_mail_log.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in sent_items),
                encoding="utf-8",
            )
            with patch("src.jobs.access.resolve_job_paths", side_effect=resolver):
                assign_job_owner(job_id, Principal("alice", "tenant-a"))

            controller = JobsWebController(
                check_auth=lambda: Principal("alice", "tenant-a"),
                settings=SimpleNamespace(upload_data_max_bytes=1024, upload_template_max_bytes=1024),
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None),
                prefer_existing_file=lambda primary, fallback: primary if primary.exists() else fallback,
                validate_uploaded_file=lambda *args, **kwargs: "file.xlsx",
                cached_excel_row_count=lambda path: 48,
                cached_tree_file_count=lambda path, pattern: 0,
                safe_int=lambda value: int(value or 0),
                create_job_id=lambda: "job-new",
                resolve_job_paths=resolver,
                jobs_dir=jobs_dir,
                create_documents_load_test_job=lambda **kwargs: {},
                start_parser_verification_process=lambda **kwargs: None,
                get_parser_status=lambda job_id: {},
                get_generator_status=lambda job_id: {},
                get_philologist_status=lambda job_id, include_details=False: {},
                get_sender_status=lambda job_id: {},
                run_parser_municipality_verification=lambda *args, **kwargs: {},
            )
            app = FastAPI()
            app.include_router(controller.router)

            with patch("src.jobs.access.resolve_job_paths", side_effect=resolver):
                response = TestClient(app).get("/api/jobs/history")

        self.assertEqual(response.status_code, 200)
        jobs = response.json()["result"]["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["sent_rows"], 34)
        self.assertEqual(jobs[0]["total_rows"], 48)
        self.assertEqual(jobs[0]["campaign_title"], "СТП_Регионы на буквы Е,З,И_48")

    def test_consent_request_records_owner_scope_and_expired_token_cannot_confirm(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            consent_path = tmpdir / "jobs" / "job-a" / "state" / "consents.json"
            consents_dir = tmpdir / "jobs" / "job-a" / "consents"
            job_dir = consent_path.parent.parent
            with (
                patch.object(consent_store, "_consent_path", return_value=consent_path),
                patch.object(consent_store, "_consent_documents_dir", return_value=consents_dir),
                patch.object(consent_store, "_iter_job_dirs", return_value=[job_dir]),
                patch.object(consent_store, "read_job_owner", return_value={"owner_username": "alice", "tenant_id": "tenant-a"}),
                patch.object(consent_store.settings, "public_base_url", "https://example.test"),
            ):
                record = consent_store.prepare_consent_request(
                    job_id="job-a",
                    row={"ID": 42, "MUN_NAME": "Тестовый район"},
                    recipient="User@Example.com",
                    transport="smtp",
                )
                self.assertEqual(record["tenant_id"], "tenant-a")
                self.assertEqual(record["owner_username"], "alice")
                self.assertEqual(record["recipient_key"], "user@example.com")
                self.assertIn("expires_at", record)

                payload = json.loads(consent_path.read_text(encoding="utf-8"))
                payload["records"][0]["expires_at"] = "2000-01-01T00:00:00"
                consent_path.write_text(json.dumps(payload), encoding="utf-8")

                confirmed = consent_store.confirm_consent(record["token"], ip="127.0.0.1", user_agent="test")

                self.assertTrue(confirmed["_expired"])
                self.assertEqual(confirmed["status"], "expired")
                self.assertFalse(
                    consent_store.has_confirmed_consent(
                        job_id="job-a",
                        row_id=42,
                        recipient="user@example.com",
                    )
                )

    def test_workers_stop_rejects_cross_owner_status_path(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            jobs_dir = tmpdir / "jobs"
            status_path = jobs_dir / "job-a" / "state" / "worker-sender-abc.status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text("{}", encoding="utf-8")
            resolver = lambda job_id=None: _job_paths(tmpdir, job_id)
            with patch("src.jobs.access.resolve_job_paths", side_effect=resolver):
                assign_job_owner("job-a", Principal("alice", "tenant-a"))

                calls: list[dict] = []
                app = FastAPI()
                app.include_router(
                    create_workers_router(
                        check_auth=lambda: Principal("bob", "tenant-a"),
                        jobs_dir=jobs_dir,
                        list_worker_statuses=lambda *args, **kwargs: [],
                        terminate_worker_process=lambda **kwargs: calls.append(kwargs) or {"terminated": True},
                    )
                )
                response = TestClient(app).post(
                    "/api/workers/stop",
                    json={"status_path": str(status_path), "pid": 123},
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(calls, [])

    def test_append_audit_event_writes_actor_and_action(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            audit_path = tmpdir / "audit.jsonl"
            ok = append_audit_event(
                action="sender.stop",
                principal=Principal("alice", "tenant-a"),
                job_id="job-a",
                details={"reason": "test"},
                audit_log_path=audit_path,
            )

            self.assertTrue(ok)
            record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["action"], "sender.stop")
            self.assertEqual(record["actor"]["tenant_id"], "tenant-a")
            self.assertEqual(record["details"], {"reason": "test"})


if __name__ == "__main__":
    unittest.main()