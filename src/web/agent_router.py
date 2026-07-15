from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from src.jobs.access import JobAccessDenied, require_admin
from src.web.request_models import ChatRequest, InflectionApprovalRequest


def create_agent_router(
    *,
    check_auth: Callable[..., str],
    upsert_override: Callable[..., dict],
    get_autonomous_worker_state: Callable[[], dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    def ensure_admin(principal: object) -> None:
        try:
            require_admin(principal)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post("/api/agent-memory/approve-inflection")
    def approve_inflection_memory(payload: InflectionApprovalRequest = Body(...), principal: object = Depends(check_auth)):
        ensure_admin(principal)
        try:
            result = upsert_override(
                entity_type=payload.entity_type,
                source_value=payload.source_value,
                target_case=payload.target_case,
                result_value=payload.result_value,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "result": result}

    @router.get("/api/orchestrator/status")
    def orchestrator_status(session_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_admin(principal)
        raise HTTPException(status_code=404, detail="Оркестратор отключён в этой ветке.")

    @router.get("/api/autonomous-worker/status")
    def autonomous_worker_status(principal: object = Depends(check_auth)):
        ensure_admin(principal)
        return {"status": "ok", "result": get_autonomous_worker_state()}

    @router.post("/api/orchestrator/chat")
    def orchestrator_chat(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        ensure_admin(principal)
        raise HTTPException(status_code=404, detail="Оркестратор отключён в этой ветке.")

    return router
