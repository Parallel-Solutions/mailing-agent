from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.generator.generation.document_builder import OUTPUT_FOLDER_MANIFEST_FILENAME
from src.web.download_sources import legacy_parser_output_dir
from src.web.preview_router import create_preview_router


@contextmanager
def _workspace_temp_dir():
    root = Path(__file__).resolve().parents[1] / f"test-preview-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class PreviewRouterTests(unittest.TestCase):
    def _client(self, *, latest_matching_file=None, parser_verified: bool = True) -> TestClient:
        app = FastAPI()
        parser_status = (
            {"municipality_name_verification_state": {"status": "completed"}}
            if parser_verified
            else {"municipality_name_verification_state": {"status": "idle"}}
        )
        app.include_router(
            create_preview_router(
                check_auth=lambda: "tester",
                latest_matching_file=latest_matching_file or (lambda *args, **kwargs: None),
                resolve_cached_output_archive=lambda job_id: (Path("missing.zip"), False),
                build_output_archive=lambda job_id: Path("missing.zip"),
                is_cache_fresh=lambda *args, **kwargs: True,
                job_state_dir=lambda job_id: Path("state"),
                get_parser_status=lambda job_id: parser_status,
                safe_int=lambda value, default=0: int(value or default),
                output_archive_ready=lambda job_id: False,
            )
        )
        return TestClient(app)

    def test_meta_for_data_xlsx(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            data_path = tmpdir / "input" / "data.xlsx"
            data_path.parent.mkdir(parents=True)
            workbook = Workbook()
            sheet = workbook.active
            sheet["A1"] = "ID"
            sheet["A2"] = "1"
            workbook.save(data_path)
            workbook.close()

            client = self._client()
            paths = SimpleNamespace(
                root_dir=tmpdir,
                data_xlsx=data_path,
                output_dir=tmpdir / "output",
            )
            with patch("src.web.download_sources.resolve_job_paths", return_value=paths), patch(
                "src.web.download_sources.ensure_local_job_path",
                return_value=data_path,
            ), patch("src.jobs.clients_store.prepare_data_xlsx", return_value=data_path):
                response = client.get("/api/preview/meta?kind=data-xlsx&job_id=job-a")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["result"]
        self.assertEqual(payload["kind"], "data-xlsx")
        self.assertEqual(payload["preview_mode"], "table")
        self.assertIn("/api/download/data-xlsx", payload["download_url"])

    def test_table_preview_for_xlsx(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            data_path = tmpdir / "input" / "data.xlsx"
            data_path.parent.mkdir(parents=True)
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["ID", "NAME"])
            sheet.append(["1", "Alpha"])
            sheet.append(["2", "Beta"])
            workbook.save(data_path)
            workbook.close()

            client = self._client()
            paths = SimpleNamespace(
                root_dir=tmpdir,
                data_xlsx=data_path,
                output_dir=tmpdir / "output",
            )
            with patch("src.web.download_sources.resolve_job_paths", return_value=paths), patch(
                "src.web.download_sources.ensure_local_job_path",
                return_value=data_path,
            ), patch("src.jobs.clients_store.prepare_data_xlsx", return_value=data_path):
                response = client.get("/api/preview/table?kind=data-xlsx&job_id=job-a")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["result"]
        self.assertEqual(payload["columns"], ["ID", "NAME"])
        self.assertEqual(len(payload["rows"]), 2)

    def test_text_preview_for_agent_report(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            report_path = tmpdir / "agent_report.txt"
            report_path.write_text("line one\nline two", encoding="utf-8")

            client = self._client()
            with patch(
                "src.web.download_sources.build_agent_report",
                return_value="line one\nline two",
            ), patch(
                "src.web.download_sources.get_agent_report_path",
                return_value=report_path,
            ), patch("src.web.download_sources.save_agent_report", return_value=None):
                response = client.get("/api/preview/text?kind=agent-report&job_id=job-a")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["result"]
        self.assertIn("line one", payload["content"])

    def test_archive_preview_lists_output_files(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            output_dir = tmpdir / "output" / "001_test"
            output_dir.mkdir(parents=True)
            (output_dir / "КП_test.pdf").write_bytes(b"%PDF-1.4")
            (output_dir / OUTPUT_FOLDER_MANIFEST_FILENAME).write_text(
                json.dumps({"mun_name": "Test MO"}),
                encoding="utf-8",
            )

            client = self._client()
            paths = SimpleNamespace(output_dir=tmpdir / "output")
            with patch("src.web.download_sources.resolve_job_paths", return_value=paths):
                response = client.get("/api/preview/archive?kind=output&job_id=job-a")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["result"]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["entries"][0]["ext"], ".pdf")
        self.assertIn("Test MO", payload["entries"][0]["label"])

    def test_preview_file_rejects_path_traversal(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            output_dir = tmpdir / "output"
            output_dir.mkdir()
            (output_dir / "doc.pdf").write_bytes(b"%PDF-1.4")

            client = self._client()
            paths = SimpleNamespace(output_dir=output_dir)
            with patch("src.web.download_sources.resolve_job_paths", return_value=paths):
                response = client.get("/api/preview/file?kind=output&job_id=job-a&path=../secret.pdf")

        self.assertEqual(response.status_code, 400)

    def test_preview_file_serves_pdf_inline(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            output_dir = tmpdir / "output" / "folder"
            output_dir.mkdir(parents=True)
            pdf_path = output_dir / "doc.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 test")

            client = self._client()
            paths = SimpleNamespace(output_dir=tmpdir / "output")
            with patch("src.web.download_sources.resolve_job_paths", return_value=paths):
                response = client.get("/api/preview/file?kind=output&job_id=job-a&path=folder/doc.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4 test")
        self.assertIn("inline", response.headers["content-disposition"])

    def test_unknown_kind_returns_400(self) -> None:
        client = self._client()
        response = client.get("/api/preview/meta?kind=unknown")
        self.assertEqual(response.status_code, 400)

    def test_legacy_parser_output_dir_resolves_without_recursion(self) -> None:
        self.assertTrue(
            legacy_parser_output_dir().as_posix().endswith("src/parser_new/output/latest")
        )


if __name__ == "__main__":
    unittest.main()
