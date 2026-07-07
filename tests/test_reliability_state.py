from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.generation.document_builder import build_output_folder_name, write_output_folder_manifest
from src.generator.generation.generator_agent import cleanup_existing_output_dirs
from sqlalchemy import text

from src.infra.db import engine
from src.jobs import load_agent_state, resolve_job_paths
from src.security.auth import Principal
from src.web.parser_router import create_parser_router
from tests.bootstrap import reset_test_database


class ReliabilityStateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_corrupt_state_returns_explicit_diagnostic(self) -> None:
        job_id = f"job-rs-corrupt-{uuid4().hex}"
        paths = resolve_job_paths(job_id)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO agent_states (job_id, agent_name, state, updated_at) "
                        "VALUES (:job_id, 'parser', '\"broken\"'::jsonb, NOW())"
                    ),
                    {"job_id": job_id},
                )

            state = load_agent_state("parser", {"status": "idle", "summary_text": "idle"}, job_id=job_id)

            self.assertEqual(state["status"], "error")
            self.assertEqual(state["state_error"], "invalid_json_shape")
            self.assertIn("поврежден", state["summary_text"])
        finally:
            shutil.rmtree(paths.root_dir, ignore_errors=True)

    def test_cleanup_output_dirs_uses_exact_name_or_manifest_not_row_prefix(self) -> None:
        output_dir = Path("tmp") / f"test-rs-output-{uuid4().hex}"
        try:
            output_dir.mkdir(parents=True)
            target_row = {"ID": "1", "MUN_NAME": "Alpha"}
            prefix_collision_row = {"ID": "1_2", "MUN_NAME": "Beta"}

            collision_dir = output_dir / build_output_folder_name(prefix_collision_row)
            collision_dir.mkdir(parents=True)
            (collision_dir / "keep.docx").write_text("keep", encoding="utf-8")

            stale_same_row_dir = output_dir / "old-safe-name"
            stale_same_row_dir.mkdir()
            write_output_folder_manifest(stale_same_row_dir, {"ID": "1", "MUN_NAME": "Old Alpha"})

            exact_target_dir = output_dir / build_output_folder_name(target_row)
            exact_target_dir.mkdir()
            (exact_target_dir / "old.docx").write_text("old", encoding="utf-8")

            cleanup_existing_output_dirs([target_row], output_dir)

            self.assertTrue(collision_dir.exists())
            self.assertFalse(stale_same_row_dir.exists())
            self.assertFalse(exact_target_dir.exists())
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


class ParserRouterReliabilityTests(unittest.TestCase):
    def _client(self, *, existing_thread: object | None = None):
        calls: list[dict] = []
        state = {"status": "idle", "summary_text": "idle"}

        def check_auth() -> Principal:
            return Principal(username="alice", tenant_id="tenant-a", role="user")

        def start_parser_thread_if_absent(job_id: str | None, **kwargs):
            calls.append({"job_id": job_id, **kwargs})
            state.update({"status": "running", "summary_text": "Парсер запущен в фоне."})
            return object(), True

        def get_parser_status(job_id: str | None):
            return dict(state, job_id=job_id)

        app = FastAPI()
        logger = SimpleNamespace(exception=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        app.include_router(
            create_parser_router(
                check_auth=check_auth,
                parse_optional_limit=lambda payload: None if payload is None else payload.get("limit"),
                start_parser_thread_if_absent=start_parser_thread_if_absent,
                parser_job_key=lambda job_id: str(job_id or "__legacy__"),
                get_parser_thread=lambda job_id: existing_thread,
                run_parser_agent=lambda **kwargs: {},
                get_parser_status=get_parser_status,
                run_parser_municipality_verification=lambda *args, **kwargs: {},
                format_municipality_verification_for_chat=lambda *args, **kwargs: "",
                parser_progress_subscribe=lambda job_id: iter(()),
                logger=logger,
            )
        )
        return TestClient(app), calls

    def test_parser_start_returns_accepted_without_running_parser_inline(self) -> None:
        client, calls = self._client()
        with patch("src.web.parser_router.append_audit_event", lambda **kwargs: None):
            response = client.post("/api/parser/start", json={"job_id": "job-rs-parser"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertTrue(payload["started"])
        self.assertEqual(payload["result"]["status"], "running")
        self.assertEqual(calls[0]["task"], "parser_start")

    def test_parser_run_schedules_parser_agent_worker(self) -> None:
        client, calls = self._client()
        with patch("src.web.parser_router.append_audit_event", lambda **kwargs: None):
            response = client.post("/api/parser/run", json={"job_id": "job-rs-parser", "limit": 3})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["task"], "parser_agent")
        self.assertEqual(calls[0]["kwargs"], {"job_id": "job-rs-parser", "limit": 3})

    def test_parser_run_rejects_invalid_limit_before_scheduling_worker(self) -> None:
        client, calls = self._client()

        response = client.post("/api/parser/run", json={"job_id": "job-rs-parser", "limit": "not-a-number"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_parser_start_rejects_unknown_payload_fields(self) -> None:
        client, calls = self._client()

        response = client.post("/api/parser/start", json={"job_id": "job-rs-parser", "unexpected": True})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_parser_start_reuses_existing_worker(self) -> None:
        client, calls = self._client(existing_thread=object())

        response = client.post("/api/parser/start", json={"job_id": "job-rs-parser"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["started"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
