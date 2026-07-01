from __future__ import annotations

import threading
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.jobs.audit import append_audit_event
from src.web.request_models import ChatRequest, JobScopedRequest, PhilologistRunRequest
from src.web.responses import ok_response


def create_philologist_router(
    *,
    check_auth: Callable[..., str],
    get_philologist_thread: Callable[[str | None], threading.Thread | None],
    compact_philologist_status: Callable[[dict], dict],
    get_philologist_status: Callable[[str | None], dict],
    clear_philologist_stop_request: Callable[[str | None], Any],
    prime_philologist_running_state: Callable[[str | None, str | None], dict],
    run_philologist_background: Callable[..., None],
    philologist_job_key: Callable[[str | None], str],
    register_philologist_thread: Callable[[str | None, threading.Thread], None],
    request_philologist_stop: Callable[[str | None], dict],
    build_philologist_plan: Callable[[str | None], dict],
    chat_with_philologist: Callable[..., dict],
    ensure_user_inprocess_limit: Callable[[str | None], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post("/api/philologist/run")
    async def philologist_run(payload: PhilologistRunRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        payload = payload or PhilologistRunRequest()
        ai_enabled = payload.ai_enabled
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        mode = str(payload.mode or "").strip().lower() or None

        existing_thread = get_philologist_thread(job_id)
        if existing_thread:
            return {"status": "ok", "result": compact_philologist_status(get_philologist_status(job_id))}

        existing_state = get_philologist_status(job_id)
        if str(existing_state.get("status") or "") in {"running", "finalizing"}:
            return {"status": "ok", "result": compact_philologist_status(existing_state)}

        clear_philologist_stop_request(job_id)
        primed_state = prime_philologist_running_state(job_id, mode or "fast")
        if ensure_user_inprocess_limit is not None:
            try:
                ensure_user_inprocess_limit(job_id)
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        philologist_thread = threading.Thread(
            target=run_philologist_background,
            kwargs={"ai_enabled": ai_enabled, "job_id": job_id, "mode": mode},
            daemon=True,
            name=f"philologist-{philologist_job_key(job_id)}",
        )
        register_philologist_thread(job_id, philologist_thread)
        philologist_thread.start()
        append_audit_event(action="philologist.start", principal=principal, job_id=job_id, details={"mode": mode or "fast"})
        return {"status": "ok", "result": primed_state}

    @router.get("/api/philologist/status")
    async def philologist_status(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {"status": "ok", "result": compact_philologist_status(get_philologist_status(job_id))}

    @router.post("/api/philologist/stop")
    async def philologist_stop(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = request_philologist_stop(job_id)
        append_audit_event(action="philologist.stop", principal=principal, job_id=job_id)
        return {"status": "ok", "result": compact_philologist_status(result)}

    @router.get("/api/philologist/plan")
    async def philologist_plan(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {"status": "ok", "result": build_philologist_plan(job_id)}

    @router.post("/api/philologist/chat")
    async def philologist_chat(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = chat_with_philologist(message, job_id=job_id)
        return ok_response(result, **result)

    return router
