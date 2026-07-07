from __future__ import annotations

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

from src.generator.generation.document_builder import OUTPUT_FOLDER_MANIFEST_FILENAME
from src.web.download_router import create_download_router, legacy_parser_output_dir


@contextmanager
def _workspace_temp_dir() -> Iterator[Path]:
    root = Path(__file__).resolve().parents[1] / f"test-download-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class DownloadRouterIsolationTests(unittest.TestCase):
    def test_legacy_parser_output_dir_resolves_without_recursion(self) -> None:
        self.assertTrue(str(legacy_parser_output_dir()).endswith("src\\parser_new\\output\\latest"))
    def _client(self, *, latest_matching_file, parser_verified: bool = True, output_archive_ready=None) -> TestClient:
        app = FastAPI()
        parser_status = (
            {"municipality_name_verification_state": {"status": "completed"}}
            if parser_verified
            else {"municipality_name_verification_state": {"status": "idle"}}
        )
        app.include_router(
            create_download_router(
                check_auth=lambda: "tester",
                prefer_existing_file=lambda primary, fallback: primary if primary.exists() else fallback,
                latest_matching_file=latest_matching_file,
                resolve_cached_output_archive=lambda job_id: (Path("missing.zip"), False),
                build_output_archive=lambda job_id: Path("missing.zip"),
                is_cache_fresh=lambda *args, **kwargs: False,
                job_state_dir=lambda job_id: Path("state"),
                get_parser_status=lambda job_id: parser_status,
                safe_int=lambda value, default=0: int(value or default),
                output_archive_ready=output_archive_ready,
            )
        )
        return TestClient(app)

    def test_parser_result_with_job_id_does_not_fallback_to_global_latest(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            job_output = tmpdir / "jobs" / "job-a" / "output"
            job_output.mkdir(parents=True)
            global_output = tmpdir / "parser_new" / "output" / "latest"
            global_output.mkdir(parents=True)
            global_file = global_output / "batch_global.xlsx"
            global_file.write_bytes(b"global")
            searched_dirs: list[tuple[Path, ...]] = []

            def latest_matching_file(directories: list[Path], *, pattern: str, exclude_substring: str | None = None):
                searched_dirs.append(tuple(directories))
                if any(path == global_output for path in directories):
                    return global_file
                return None

            client = self._client(latest_matching_file=latest_matching_file)
            paths = SimpleNamespace(output_dir=job_output)
            with patch("src.web.download_router.resolve_job_paths", return_value=paths):
                response = client.get("/api/parser/download-result?job_id=job-a")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(searched_dirs, [(job_output,)])

    def test_parser_failed_with_job_id_does_not_search_global_latest(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            job_output = tmpdir / "jobs" / "job-a" / "output"
            job_output.mkdir(parents=True)
            global_output = tmpdir / "parser_new" / "output" / "latest"
            global_output.mkdir(parents=True)
            global_file = global_output / "batch_FAILED_global.xlsx"
            global_file.write_bytes(b"global")
            searched_dirs: list[tuple[Path, ...]] = []

            def latest_matching_file(directories: list[Path], *, pattern: str, exclude_substring: str | None = None):
                searched_dirs.append(tuple(directories))
                if any(path == global_output for path in directories):
                    return global_file
                return None

            client = self._client(latest_matching_file=latest_matching_file)
            paths = SimpleNamespace(output_dir=job_output)
            with patch("src.web.download_router.resolve_job_paths", return_value=paths):
                response = client.get("/api/parser/download-failed?job_id=job-a")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(searched_dirs, [(job_output,)])

    def test_parser_result_without_job_id_keeps_legacy_global_latest(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            parser_output = tmpdir / "src" / "parser_new" / "output" / "latest"
            parser_output.mkdir(parents=True)
            parser_file = parser_output / "batch_global.xlsx"
            parser_file.write_bytes(b"global")
            searched_dirs: list[tuple[Path, ...]] = []

            def latest_matching_file(directories: list[Path], *, pattern: str, exclude_substring: str | None = None):
                searched_dirs.append(tuple(directories))
                return parser_file if directories == [parser_output] else None

            client = self._client(latest_matching_file=latest_matching_file)
            with patch("src.web.download_router.legacy_parser_output_dir", return_value=parser_output):
                response = client.get("/api/parser/download-result")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"global")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(response.headers["expires"], "0")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(searched_dirs, [(parser_output,)])


    def test_output_download_waits_until_archive_is_ready(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            job_output = tmpdir / "jobs" / "job-a" / "output"
            job_output.mkdir(parents=True)
            (job_output / "document.docx").write_bytes(b"docx")

            client = self._client(
                latest_matching_file=lambda *args, **kwargs: None,
                output_archive_ready=lambda job_id: False,
            )
            paths = SimpleNamespace(output_dir=job_output)
            with patch("src.web.download_router.resolve_job_paths", return_value=paths):
                response = client.get("/api/download/output?job_id=job-a")

        self.assertEqual(response.status_code, 409)
        self.assertIn("Документы ещё собираются", response.json()["detail"])
    def test_output_download_rejects_manifest_only_output(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            job_output = tmpdir / "jobs" / "job-a" / "output"
            job_output.mkdir(parents=True)
            (job_output / OUTPUT_FOLDER_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

            client = self._client(latest_matching_file=lambda *args, **kwargs: None)
            paths = SimpleNamespace(output_dir=job_output)
            with patch("src.web.download_router.resolve_job_paths", return_value=paths):
                response = client.get("/api/download/output?job_id=job-a")

        self.assertEqual(response.status_code, 404)
        self.assertIn("Готовые документы не найдены", response.json()["detail"])

if __name__ == "__main__":
    unittest.main()
