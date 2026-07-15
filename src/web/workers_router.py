from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from src.jobs.access import JobAccessDenied, authorize_job_access, job_is_visible
from src.jobs.audit import append_audit_event
from src.web.request_models import WorkerStopRequest


def _normalize_worker_status_path(value: Any, jobs_dir: Path) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    try:
        resolved_path = Path(raw_value).resolve(strict=False)
        jobs_root = jobs_dir.resolve(strict=False)
        resolved_path.relative_to(jobs_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректный status_path worker-процесса.") from exc

    if not resolved_path.name.startswith("worker-") or not resolved_path.name.endswith(".status.json"):
        raise HTTPException(status_code=400, detail="Некорректный status_path worker-процесса.")
    return str(resolved_path)


def _job_id_from_status_path(status_path: str, jobs_dir: Path) -> str | None:
    try:
        relative_path = Path(status_path).resolve(strict=False).relative_to(jobs_dir.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректный status_path worker-процесса.") from exc
    parts = relative_path.parts
    job_id = parts[0] if parts else None
    return None if job_id == "__legacy__" else job_id


def create_workers_router(
    *,
    check_auth: Callable[..., str],
    jobs_dir: Path,
    list_worker_statuses: Callable[..., list[dict[str, Any]]],
    terminate_worker_process: Callable[..., dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get("/api/workers/status")
    def workers_status(limit: int = 100, principal: object = Depends(check_auth)):
        safe_limit = max(1, min(int(limit or 100), 500))
        visible_workers = [
            status
            for status in list_worker_statuses(jobs_dir, limit=safe_limit)
            if job_is_visible(str(status.get("job_id") or "").strip() or None, principal)
        ]
        return {
            "status": "ok",
            "result": {
                "workers": visible_workers,
            },
        }

    @router.post("/api/workers/stop")
    def workers_stop(payload: WorkerStopRequest = Body(...), principal: object = Depends(check_auth)):
        status_path = _normalize_worker_status_path(payload.status_path, jobs_dir)
        if not status_path:
            raise HTTPException(status_code=400, detail="Не указан status_path активного worker-процесса.")
        job_id = _job_id_from_status_path(status_path, jobs_dir)
        ensure_job_access(job_id, principal)

        pid = payload.pid
        try:
            result = terminate_worker_process(status_path=status_path, pid=pid)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event(action="worker.stop", principal=principal, job_id=job_id, details={"status_path": status_path, "pid": pid})
        return {"status": "ok", "result": result}

    return router
