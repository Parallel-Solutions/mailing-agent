from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.infra.readiness import collect_readiness
from src.web.public_router import create_public_router
from src.workers.healthcheck import check_heartbeat, touch_heartbeat


class ReadinessTests(unittest.TestCase):
    def test_collect_readiness_ok(self) -> None:
        with (
            patch("src.infra.readiness.check_database"),
            patch("src.infra.readiness.check_redis"),
            patch("src.infra.readiness.check_object_store"),
            patch("src.infra.readiness.check_gotenberg"),
        ):
            payload = collect_readiness()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "up")
        self.assertEqual(payload["redis"], "up")
        self.assertEqual(payload["object_store"], "up")
        self.assertEqual(payload["gotenberg"], "up")
        self.assertNotIn("detail", payload)

    def test_collect_readiness_partial_failure(self) -> None:
        with (
            patch("src.infra.readiness.check_database"),
            patch("src.infra.readiness.check_redis", side_effect=RuntimeError("boom")),
            patch("src.infra.readiness.check_object_store"),
            patch("src.infra.readiness.check_gotenberg"),
        ):
            payload = collect_readiness()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["database"], "up")
        self.assertEqual(payload["redis"], "down")
        self.assertEqual(payload["detail"]["redis"], "RuntimeError")

    def test_ready_endpoint(self) -> None:
        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app)
        with patch("src.infra.readiness.collect_readiness", return_value={
            "status": "ok",
            "database": "up",
            "redis": "up",
            "object_store": "up",
            "gotenberg": "up",
        }):
            response = client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_ready_endpoint_503(self) -> None:
        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app)
        with patch("src.infra.readiness.collect_readiness", return_value={
            "status": "error",
            "database": "up",
            "redis": "down",
            "object_store": "up",
            "gotenberg": "up",
            "detail": {"redis": "ConnectionError"},
        }):
            response = client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["redis"], "down")

    def test_worker_heartbeat(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb"
            touch_heartbeat(path)
            check_heartbeat(path, max_age_seconds=30.0)
            with self.assertRaises(RuntimeError):
                check_heartbeat(path, max_age_seconds=-1.0)


if __name__ == "__main__":
    unittest.main()
