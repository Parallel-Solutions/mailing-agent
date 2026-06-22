from __future__ import annotations

import secrets
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.jobs.json_store import read_json, write_json_atomic


StateDirFactory = Callable[[str | None], Path]
JobKeyFactory = Callable[[str | None], str]
UnregisterCallback = Callable[[str | None], None]
FailureCallback = Callable[[str, str | None, str], None]

_ACTIVE_WORKERS_LOCK = threading.Lock()
_ACTIVE_WORKERS: dict[str, subprocess.Popen] = {}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload)


def _write_worker_payload(job_id: str | None, task: str, kwargs: dict | None, state_dir_factory: StateDirFactory) -> Path:
    state_dir = state_dir_factory(job_id)
    payload_path = state_dir / f"worker-{task}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}.json"
    _write_json(payload_path, {"task": task, "kwargs": _json_safe(kwargs or {})})
    return payload_path


def _write_worker_status(
    status_path: Path,
    *,
    task: str,
    job_id: str | None,
    status: str,
    payload_path: Path,
    pid: int | None = None,
    return_code: int | None = None,
    message: str | None = None,
    started_at: str | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> None:
    payload: dict[str, Any] = {
        "task": task,
        "job_id": job_id,
        "status": status,
        "payload_path": str(payload_path),
        "pid": pid,
        "return_code": return_code,
        "message": message,
        "started_at": started_at,
        "updated_at": _now(),
        "worker_id": payload_path.stem,
        "stdout_path": str(stdout_path) if stdout_path else "",
        "stderr_path": str(stderr_path) if stderr_path else "",
    }
    if status in {"completed", "error"}:
        payload["completed_at"] = _now()
    _write_json(status_path, payload)


def _run_worker_process_monitor(
    *,
    task: str,
    payload_path: Path,
    status_path: Path,
    job_id: str | None,
    project_root: Path,
    unregister: UnregisterCallback,
    mark_failed: FailureCallback,
    logger: Any,
    timeout_seconds: int = 0,
) -> None:
    started_at = _now()
    stdout_path = payload_path.with_suffix(".out.log")
    stderr_path = payload_path.with_suffix(".err.log")
    active_key = str(status_path)
    stdout_handle = None
    stderr_handle = None
    try:
        command = [
            sys.executable,
            "-m",
            "src.workers.background_worker",
            "--payload",
            str(payload_path),
        ]
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_handle = stdout_path.open("ab")
        stderr_handle = stderr_path.open("ab")
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        with _ACTIVE_WORKERS_LOCK:
            _ACTIVE_WORKERS[active_key] = process
        _write_worker_status(
            status_path,
            task=task,
            job_id=job_id,
            status="running",
            payload_path=payload_path,
            pid=process.pid,
            started_at=started_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        try:
            return_code = process.wait(timeout=timeout_seconds if timeout_seconds > 0 else None)
        except subprocess.TimeoutExpired:
            message = f"worker process exceeded timeout {timeout_seconds} seconds"
            logger.error("worker_process_timeout", task=task, job_id=job_id, pid=process.pid, timeout_seconds=timeout_seconds)
            process.terminate()
            try:
                return_code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait()
            _write_worker_status(
                status_path,
                task=task,
                job_id=job_id,
                status="error",
                payload_path=payload_path,
                pid=process.pid,
                return_code=return_code,
                message=message,
                started_at=started_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            mark_failed(task, job_id, message)
            return
        if return_code == 0:
            _write_worker_status(
                status_path,
                task=task,
                job_id=job_id,
                status="completed",
                payload_path=payload_path,
                pid=process.pid,
                return_code=return_code,
                started_at=started_at,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            return

        message = f"worker process exited with code {return_code}"
        logger.error(
            "worker_process_failed",
            task=task,
            job_id=job_id,
            return_code=return_code,
            payload=str(payload_path),
            pid=process.pid,
        )
        _write_worker_status(
            status_path,
            task=task,
            job_id=job_id,
            status="error",
            payload_path=payload_path,
            pid=process.pid,
            return_code=return_code,
            message=message,
            started_at=started_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        mark_failed(task, job_id, message)
    except Exception as exc:
        message = f"worker process did not start: {type(exc).__name__}: {exc}"
        logger.exception("worker_process_start_failed", task=task, job_id=job_id, payload=str(payload_path))
        _write_worker_status(
            status_path,
            task=task,
            job_id=job_id,
            status="error",
            payload_path=payload_path,
            message=message,
            started_at=started_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        mark_failed(task, job_id, message)
    finally:
        with _ACTIVE_WORKERS_LOCK:
            _ACTIVE_WORKERS.pop(active_key, None)
        if stdout_handle is not None:
            try:
                stdout_handle.close()
            except Exception:
                pass
        if stderr_handle is not None:
            try:
                stderr_handle.close()
            except Exception:
                pass
        unregister(job_id)


def start_worker_process_thread(
    job_id: str | None,
    *,
    task: str,
    kwargs: dict | None = None,
    name: str | None = None,
    registry: dict[str, threading.Thread],
    registry_lock: threading.Lock,
    key_factory: JobKeyFactory,
    unregister: UnregisterCallback,
    state_dir_factory: StateDirFactory,
    project_root: Path,
    mark_failed: FailureCallback,
    logger: Any,
    max_workers: int = 1,
    timeout_seconds: int = 0,
    before_start: Callable[[], None] | None = None,
) -> tuple[threading.Thread, bool]:
    with registry_lock:
        key = key_factory(job_id)
        existing = registry.get(key)
        if existing and existing.is_alive():
            return existing, False
        if existing and not existing.is_alive():
            registry.pop(key, None)
        active_count = sum(1 for item in registry.values() if item.is_alive())
        if max_workers > 0 and active_count >= max_workers:
            raise RuntimeError(
                f"Сервер уже выполняет максимум worker-процессов для задачи {task}: {active_count}/{max_workers}."
            )
        if before_start is not None:
            before_start()

        payload_path = _write_worker_payload(job_id, task, kwargs, state_dir_factory)
        status_path = payload_path.with_suffix(".status.json")
        _write_worker_status(
            status_path,
            task=task,
            job_id=job_id,
            status="queued",
            payload_path=payload_path,
        )
        thread = threading.Thread(
            target=_run_worker_process_monitor,
            kwargs={
                "task": task,
                "payload_path": payload_path,
                "status_path": status_path,
                "job_id": job_id,
                "project_root": project_root,
                "unregister": unregister,
                "mark_failed": mark_failed,
                "logger": logger,
                "timeout_seconds": timeout_seconds,
            },
            daemon=True,
            name=name or f"{task}-{key}",
        )
        registry[key] = thread
        thread.start()
        return thread, True


def list_worker_statuses(jobs_dir: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    if not jobs_dir.exists():
        return []
    for status_path in jobs_dir.glob("*/state/worker-*.status.json"):
        result = read_json(status_path, default={})
        if not result.ok or not isinstance(result.data, dict):
            continue
        data = result.data
        data["status_path"] = str(status_path)
        data["alive"] = _is_worker_active(status_path)
        statuses.append(data)
    statuses.sort(key=lambda item: str(item.get("updated_at") or item.get("started_at") or ""), reverse=True)
    return statuses[: max(1, limit)]


def terminate_worker_process(*, status_path: str | None = None, pid: int | None = None) -> dict[str, Any]:
    active_key = str(status_path or "")
    if not active_key:
        raise RuntimeError("Не указан status_path активного worker-процесса.")

    with _ACTIVE_WORKERS_LOCK:
        process = _ACTIVE_WORKERS.get(active_key)

    if process is None or process.poll() is not None:
        raise RuntimeError("Активный worker не найден или уже завершён.")

    if pid is not None and process.pid != pid:
        raise RuntimeError("PID не совпадает с активным worker-процессом.")

    process.terminate()
    return {"terminated": True, "pid": process.pid, "method": "active_process"}


def _is_worker_active(status_path: Path) -> bool:
    active_key = str(status_path)
    with _ACTIVE_WORKERS_LOCK:
        process = _ACTIVE_WORKERS.get(active_key)
        return bool(process and process.poll() is None)
