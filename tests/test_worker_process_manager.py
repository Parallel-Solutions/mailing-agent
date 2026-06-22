from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.web.workers_router import create_workers_router
from src.workers import process_manager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = PROJECT_ROOT / "storage" / "jobs"


class FakeProcess:
    def __init__(self, pid: int, return_code: int | None = None) -> None:
        self.pid = pid
        self.return_code = return_code
        self.terminated = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True


def _clear_active_workers() -> None:
    with process_manager._ACTIVE_WORKERS_LOCK:
        process_manager._ACTIVE_WORKERS.clear()


def test_terminate_worker_process_rejects_pid_only() -> None:
    fake = FakeProcess(pid=4321)
    _clear_active_workers()
    with process_manager._ACTIVE_WORKERS_LOCK:
        process_manager._ACTIVE_WORKERS["known-status-path"] = fake

    try:
        with pytest.raises(RuntimeError, match="status_path"):
            process_manager.terminate_worker_process(pid=fake.pid)
        assert fake.terminated is False
    finally:
        _clear_active_workers()


def test_terminate_worker_process_stops_only_matching_active_status_path() -> None:
    fake = FakeProcess(pid=4321)
    _clear_active_workers()
    with process_manager._ACTIVE_WORKERS_LOCK:
        process_manager._ACTIVE_WORKERS["known-status-path"] = fake

    try:
        result = process_manager.terminate_worker_process(status_path="known-status-path", pid=fake.pid)
        assert result == {"terminated": True, "pid": fake.pid, "method": "active_process"}
        assert fake.terminated is True
    finally:
        _clear_active_workers()


def test_terminate_worker_process_rejects_pid_mismatch() -> None:
    fake = FakeProcess(pid=4321)
    _clear_active_workers()
    with process_manager._ACTIVE_WORKERS_LOCK:
        process_manager._ACTIVE_WORKERS["known-status-path"] = fake

    try:
        with pytest.raises(RuntimeError, match="PID"):
            process_manager.terminate_worker_process(status_path="known-status-path", pid=9999)
        assert fake.terminated is False
    finally:
        _clear_active_workers()


def _workers_client():
    calls = []
    jobs_dir = JOBS_DIR

    def check_auth() -> str:
        return "user"

    def list_worker_statuses(*args, **kwargs):
        return []

    def terminate_worker_process(**kwargs):
        calls.append(kwargs)
        return {"terminated": True, "pid": kwargs.get("pid"), "method": "active_process"}

    app = FastAPI()
    app.include_router(
        create_workers_router(
            check_auth=check_auth,
            jobs_dir=jobs_dir,
            list_worker_statuses=list_worker_statuses,
            terminate_worker_process=terminate_worker_process,
        )
    )
    return TestClient(app), calls, jobs_dir


def test_workers_stop_requires_status_path() -> None:
    client, calls, _jobs_dir = _workers_client()

    response = client.post("/api/workers/stop", json={"pid": 4321})

    assert response.status_code == 422
    assert calls == []


def test_workers_stop_rejects_status_path_outside_jobs_dir() -> None:
    client, calls, _jobs_dir = _workers_client()
    outside_status_path = PROJECT_ROOT / "worker-documents.status.json"

    response = client.post("/api/workers/stop", json={"status_path": str(outside_status_path)})

    assert response.status_code == 400
    assert calls == []


def test_workers_stop_rejects_invalid_pid_before_termination() -> None:
    client, calls, jobs_dir = _workers_client()
    status_path = jobs_dir / "job-1" / "state" / "worker-documents-abc.status.json"

    response = client.post("/api/workers/stop", json={"status_path": str(status_path), "pid": "not-a-pid"})

    assert response.status_code == 422
    assert calls == []


def test_workers_stop_passes_normalized_job_status_path() -> None:
    client, calls, jobs_dir = _workers_client()
    status_path = jobs_dir / "job-1" / "state" / "worker-documents-abc.status.json"

    response = client.post("/api/workers/stop", json={"status_path": str(status_path), "pid": 4321})

    assert response.status_code == 200
    assert calls == [{"status_path": str(status_path.resolve(strict=False)), "pid": 4321}]
