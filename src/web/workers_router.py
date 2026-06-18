from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException


def create_workers_router(
    *,
    check_auth: Callable[..., str],
    jobs_dir: Path,
    list_worker_statuses: Callable[..., list[dict[str, Any]]],
    terminate_worker_process: Callable[..., dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workers/status")
    async def workers_status(limit: int = 100, username: str = Depends(check_auth)):
        safe_limit = max(1, min(int(limit or 100), 500))
        return {
            "status": "ok",
            "result": {
                "workers": list_worker_statuses(jobs_dir, limit=safe_limit),
            },
        }

    @router.post("/api/workers/stop")
    async def workers_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        payload = payload or {}
        status_path = str(payload.get("status_path") or "").strip() or None
        pid_value = payload.get("pid")
        try:
            pid = int(pid_value) if pid_value not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный PID worker-процесса.") from exc
        try:
            result = terminate_worker_process(status_path=status_path, pid=pid)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "result": result}

    return router
