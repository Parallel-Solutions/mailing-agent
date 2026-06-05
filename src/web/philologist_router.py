from __future__ import annotations

import threading
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException


def _job_id_from_payload(payload: dict | None) -> str | None:
    return None if payload is None else str(payload.get("job_id") or "").strip() or None


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
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(check_auth)])

    @router.post("/api/philologist/run")
    async def philologist_run(payload: dict | None = Body(default=None)):
        ai_enabled = True if payload is None else bool(payload.get("ai_enabled", True))
        job_id = _job_id_from_payload(payload)
        mode = None if payload is None else str(payload.get("mode") or "").strip().lower() or None

        existing_thread = get_philologist_thread(job_id)
        if existing_thread:
            return {"status": "ok", "result": compact_philologist_status(get_philologist_status(job_id))}

        existing_state = get_philologist_status(job_id)
        if str(existing_state.get("status") or "") in {"running", "finalizing"}:
            return {"status": "ok", "result": compact_philologist_status(existing_state)}

        clear_philologist_stop_request(job_id)
        primed_state = prime_philologist_running_state(job_id, mode or "fast")
        philologist_thread = threading.Thread(
            target=run_philologist_background,
            kwargs={"ai_enabled": ai_enabled, "job_id": job_id, "mode": mode},
            daemon=True,
            name=f"philologist-{philologist_job_key(job_id)}",
        )
        register_philologist_thread(job_id, philologist_thread)
        philologist_thread.start()
        return {"status": "ok", "result": primed_state}

    @router.get("/api/philologist/status")
    async def philologist_status(job_id: str | None = None):
        return {"status": "ok", "result": compact_philologist_status(get_philologist_status(job_id))}

    @router.post("/api/philologist/stop")
    async def philologist_stop(payload: dict | None = Body(default=None)):
        job_id = _job_id_from_payload(payload)
        return {"status": "ok", "result": compact_philologist_status(request_philologist_stop(job_id))}

    @router.get("/api/philologist/plan")
    async def philologist_plan(job_id: str | None = None):
        return {"status": "ok", "result": build_philologist_plan(job_id)}

    @router.post("/api/philologist/chat")
    async def philologist_chat(payload: dict = Body(...)):
        message = str(payload.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = str(payload.get("job_id") or "").strip() or None
        return {"status": "ok", **chat_with_philologist(message, job_id=job_id)}

    return router
