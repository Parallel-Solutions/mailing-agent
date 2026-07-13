from __future__ import annotations

import asyncio
import threading
from inspect import isawaitable
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.jobs.audit import append_audit_event
from src.web.request_models import JobScopedRequest
from src.web.responses import ok_response


def create_generator_router(
    *,
    check_auth: Callable[..., str],
    job_readiness: Callable[..., dict],
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
    ensure_user_inprocess_limit: Callable[[str | None], None] | None = None,
    start_generator_task: Callable[..., tuple[Any, bool]] | None = None,
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get("/api/counts")
    def counts(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        readiness = job_readiness(job_id=job_id, username=principal)
        if isawaitable(readiness):
            readiness = asyncio.run(readiness)
        counts_result = ((readiness or {}).get("result") or {}).get("counts") or {}
        result = {
            "parser_total": int(counts_result.get("parser_total", 0) or 0),
            "generator_total": int(counts_result.get("generator_total", 0) or 0),
            "sender_total": int(counts_result.get("sender_total", 0) or 0),
        }
        return ok_response(result, **result)

    @router.post("/api/generate")
    def generate(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        job_paths = resolve_job_paths(job_id)
        from src.jobs.clients_store import prepare_data_xlsx

        xlsx_path = prepare_data_xlsx(job_id, job_paths.data_xlsx)
        if not xlsx_path.exists():
            raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")
        existing_thread = get_generator_thread(job_id)
        if existing_thread is not None:
            return {"status": "ok", "result": compact_generator_status(get_generator_status(job_id))}

        existing_state = get_generator_status(job_id)
        if str(existing_state.get("status") or "") == "running":
            return {"status": "ok", "result": compact_generator_status(existing_state)}
        if ensure_user_inprocess_limit is not None:
            try:
                ensure_user_inprocess_limit(job_id)
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
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


        if start_generator_task is not None:
            _, started = start_generator_task(
                job_id,
                xlsx_path=xlsx_path,
                name=f"generator-{generator_job_key(job_id)}",
            )
            if not started:
                return {"status": "ok", "result": compact_generator_status(get_generator_status(job_id))}
        else:
            thread = threading.Thread(
                target=run_generator_background,
                kwargs={"xlsx_path": xlsx_path, "job_id": job_id},
                daemon=True,
                name=f"generator-{generator_job_key(job_id)}",
            )
            register_generator_thread(job_id, thread)
            thread.start()
        append_audit_event(action="generator.start", principal=principal, job_id=job_id)
        return {"status": "ok", "result": compact_generator_status(primed_state)}

    @router.get("/api/generator/status")
    def generator_status(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {"status": "ok", "result": compact_generator_status(get_generator_status(job_id))}

    @router.post("/api/generator/stop")
    def generator_stop(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = request_generator_stop(job_id)
        append_audit_event(action="generator.stop", principal=principal, job_id=job_id)
        return {"status": "ok", "result": compact_generator_status(result)}

    return router
