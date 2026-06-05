from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException


def create_agent_router(
    *,
    check_auth: Callable[..., str],
    upsert_override: Callable[..., dict],
    get_autonomous_worker_state: Callable[[], dict[str, Any]],
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(check_auth)])

    @router.post("/api/agent-memory/approve-inflection")
    async def approve_inflection_memory(payload: dict = Body(...)):
        try:
            result = upsert_override(
                entity_type=str(payload.get("entity_type") or ""),
                source_value=str(payload.get("source_value") or ""),
                target_case=str(payload.get("target_case") or ""),
                result_value=str(payload.get("result_value") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "result": result}

    @router.get("/api/orchestrator/status")
    async def orchestrator_status(session_id: str | None = None):
        raise HTTPException(status_code=404, detail="Оркестратор отключён в этой ветке.")

    @router.get("/api/autonomous-worker/status")
    async def autonomous_worker_status():
        return {"status": "ok", "result": get_autonomous_worker_state()}

    @router.post("/api/orchestrator/chat")
    async def orchestrator_chat(payload: dict = Body(...)):
        raise HTTPException(status_code=404, detail="Оркестратор отключён в этой ветке.")

    return router
