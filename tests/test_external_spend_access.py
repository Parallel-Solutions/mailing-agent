from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security.auth import Principal
from src.web.statistics_router import create_statistics_router


def _fake_stream():
    yield 'data: {"kind": "open"}\n\n'


def _make_client(principal: Principal) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_statistics_router(
            check_auth=lambda: principal,
            jobs_dir=Path("."),
            resolve_job_paths=lambda job_id=None: SimpleNamespace(root_dir=Path(".")),
            logger=SimpleNamespace(
                exception=lambda *a, **k: None,
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
            ),
            spend_ledger_subscribe=_fake_stream,
        )
    )
    return TestClient(app)


class ExternalSpendAccessTests(unittest.TestCase):
    def test_snapshot_forbidden_for_non_admin(self) -> None:
        client = _make_client(Principal("alice", "tenant-a", "user"))
        response = client.get("/api/sender/external-spend/snapshot")
        self.assertEqual(response.status_code, 403)

    def test_stream_forbidden_for_non_admin(self) -> None:
        client = _make_client(Principal("alice", "tenant-a", "user"))
        response = client.get("/api/sender/external-spend/stream")
        self.assertEqual(response.status_code, 403)

    def test_snapshot_allowed_for_admin(self) -> None:
        client = _make_client(Principal("admin", "root", "admin"))
        with patch(
            "src.web.statistics_router.build_spend_snapshot",
            return_value={
                "period_minutes": 1440,
                "total_cost_usd": 0.0,
                "total_requests": 0,
                "by_service": [],
                "by_hour": [],
                "recent_calls": [],
            },
        ) as fake_snapshot:
            response = client.get("/api/sender/external-spend/snapshot?period_minutes=60")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        fake_snapshot.assert_called_once_with(period_minutes=60)

    def test_stream_allowed_for_admin(self) -> None:
        client = _make_client(Principal("admin", "root", "admin"))
        with client.stream("GET", "/api/sender/external-spend/stream") as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            first_chunk = next(response.iter_lines())
            self.assertIn('"kind": "open"', first_chunk)


if __name__ == "__main__":
    unittest.main()
