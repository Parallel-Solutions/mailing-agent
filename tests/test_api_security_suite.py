from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.workers_router import create_workers_router


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SECURITY_TEST_MODULES = (
    "tests.test_app_security",
    "tests.test_multi_user_access",
    "tests.test_consent_store",
    "tests.test_sender_webhooks",
    "tests.test_download_router",
    "tests.test_api_request_validation",
    "tests.test_api_error_contracts",
    "tests.test_upload_validation",
    "tests.test_sender_agent",
)


class ApiSecuritySuiteGapTests(unittest.TestCase):
    def test_worker_stop_rejects_status_path_outside_jobs_dir_before_termination(self) -> None:
        calls: list[dict] = []
        jobs_dir = PROJECT_ROOT / "storage" / "jobs"
        outside_status_path = PROJECT_ROOT / "worker-documents.status.json"
        app = FastAPI()
        app.include_router(
            create_workers_router(
                check_auth=lambda: "tester",
                jobs_dir=jobs_dir,
                list_worker_statuses=lambda *args, **kwargs: [],
                terminate_worker_process=lambda **kwargs: calls.append(kwargs) or {"terminated": True},
            )
        )

        response = TestClient(app).post("/api/workers/stop", json={"status_path": str(outside_status_path)})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(ApiSecuritySuiteGapTests))
    for module_name in SECURITY_TEST_MODULES:
        suite.addTests(loader.loadTestsFromName(module_name))
    return suite


if __name__ == "__main__":
    unittest.main()
