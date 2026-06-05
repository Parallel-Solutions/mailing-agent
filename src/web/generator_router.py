from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Body, Depends, HTTPException


def create_generator_router(
    *,
    check_auth: Callable[..., str],
    job_readiness: Callable[..., Awaitable[dict]],
    prefer_existing_file: Callable[[Path, Path], Path],
    resolve_job_paths: Callable[[str | None], Any],
    get_generator_thread: Callable[[str | None], threading.Thread | None],
    compact_generator_status: Callable[[dict], dict],
    get_generator_status: Callable[[str | None], dict],
    clear_generator_stop_request: Callable[[str | None], Any],
    prime_generator_state: Callable[..., dict],
    schedule_output_archive_build: Callable[[str | None], None],
    run_generator_background: Callable[..., None],
    generator_job_key: Callable[[str | None], str],
    register_generator_thread: Callable[[str | None, threading.Thread], None],
    request_generator_stop: Callable[[str | None], dict],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/counts")
    async def counts(job_id: str | None = None, username: str = Depends(check_auth)):
        readiness = await job_readiness(job_id=job_id, username=username)
        counts_result = ((readiness or {}).get("result") or {}).get("counts") or {}
        return {
            "parser_total": int(counts_result.get("parser_total", 0) or 0),
            "generator_total": int(counts_result.get("generator_total", 0) or 0),
            "sender_total": int(counts_result.get("sender_total", 0) or 0),
        }

    @router.post("/api/generate")
    async def generate(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        job_id = str((payload or {}).get("job_id") or "").strip() or None
        xlsx_path = prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
        if not xlsx_path.exists():
            raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")
        existing_thread = get_generator_thread(job_id)
        if existing_thread is not None:
            return {"status": "ok", "result": compact_generator_status(get_generator_status(job_id))}

        existing_state = get_generator_status(job_id)
        if str(existing_state.get("status") or "") == "running":
            return {"status": "ok", "result": compact_generator_status(existing_state)}
        if str(existing_state.get("status") or "") == "stopped":
            clear_generator_stop_request(job_id)
            primed_state = existing_state
            primed_state["status"] = "running"
            primed_state["completed_at"] = None
            primed_state["summary_text"] = "Продолжаю генерацию с сохраненного места."
        else:
            primed_state = prime_generator_state(xlsx_path=xlsx_path, job_id=job_id)
        if primed_state.get("status") == "error":
            raise HTTPException(status_code=400, detail=primed_state.get("summary_text") or "Ошибка генерации")

        if primed_state.get("status") == "completed":
            schedule_output_archive_build(job_id)
            return {"status": "ok", "result": compact_generator_status(primed_state)}

        thread = threading.Thread(
            target=run_generator_background,
            kwargs={"xlsx_path": xlsx_path, "job_id": job_id},
            daemon=True,
            name=f"generator-{generator_job_key(job_id)}",
        )
        register_generator_thread(job_id, thread)
        thread.start()
        return {"status": "ok", "result": compact_generator_status(primed_state)}

    @router.get("/api/generator/status")
    async def generator_status(job_id: str | None = None, username: str = Depends(check_auth)):
        return {"status": "ok", "result": compact_generator_status(get_generator_status(job_id))}

    @router.post("/api/generator/stop")
    async def generator_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
        return {"status": "ok", "result": compact_generator_status(request_generator_stop(job_id))}

    return router
