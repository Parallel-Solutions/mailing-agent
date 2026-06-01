from __future__ import annotations

import secrets
import threading
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request


def create_sender_router(
    *,
    check_auth: Callable[..., str],
    parse_optional_limit: Callable[[dict | None], int | None],
    compact_sender_status: Callable[[dict], dict],
    get_sender_thread: Callable[[str | None], threading.Thread | None],
    clear_sender_stop_request: Callable[[str | None], Any],
    prime_sender_checking_state: Callable[[str | None, str | None], dict],
    prime_sender_running_state: Callable[[str | None, str | None], dict],
    register_sender_thread: Callable[[str | None, threading.Thread], None],
    run_sender_background: Callable[..., None],
    sender_job_key: Callable[[str | None], str],
    get_sender_status: Callable[[str | None], dict],
    get_unisender_history: Callable[..., dict],
    build_unisender_delivery_analytics: Callable[..., dict],
    settings: Any,
    append_unisender_go_events: Callable[[dict], dict],
    logger: Any,
    request_sender_stop: Callable[..., dict],
    preview_recipients: Callable[..., dict],
    chat_with_sender: Callable[..., dict[str, str]],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/sender/run")
    async def sender_run(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        dry_run = True if payload is None else bool(payload.get("dry_run", True))
        limit = parse_optional_limit(payload)
        transport = None if payload is None else payload.get("transport")
        job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None

        existing_thread = get_sender_thread(job_id)
        if existing_thread:
            return {"status": "ok", "result": compact_sender_status(get_sender_status(job_id))}

        try:
            clear_sender_stop_request(job_id)
            primed_state = (
                prime_sender_checking_state(job_id, transport)
                if dry_run
                else prime_sender_running_state(job_id, transport)
            )
            sender_thread = threading.Thread(
                target=run_sender_background,
                kwargs={"dry_run": dry_run, "limit": limit, "transport": transport, "job_id": job_id},
                daemon=True,
                name=f"sender-{sender_job_key(job_id)}",
            )
            register_sender_thread(job_id, sender_thread)
            sender_thread.start()
        except Exception as exc:
            logger.exception("sender_run_start_failed", job_id=job_id, transport=transport)
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось запустить отправку: {type(exc).__name__}: {exc}",
            ) from exc
        return {"status": "ok", "result": compact_sender_status(primed_state)}

    @router.get("/api/sender/status")
    async def sender_status(job_id: str | None = None, username: str = Depends(check_auth)):
        return {"status": "ok", "result": compact_sender_status(get_sender_status(job_id))}

    @router.get("/api/sender/unisender-history")
    async def sender_unisender_history(
        job_id: str | None = None,
        limit: int = 50,
        refresh: bool = False,
        username: str = Depends(check_auth),
    ):
        return {
            "status": "ok",
            "result": get_unisender_history(job_id=job_id, limit=limit, refresh=refresh),
        }

    @router.get("/api/sender/analytics")
    async def sender_analytics(
        job_id: str | None = None,
        refresh: bool = False,
        username: str = Depends(check_auth),
    ):
        return {
            "status": "ok",
            "result": build_unisender_delivery_analytics(job_id=job_id, refresh=refresh),
        }

    @router.get("/api/webhooks/unisender-go")
    async def unisender_go_webhook_health():
        return {"status": "ok", "message": "UniSender Go webhook endpoint is ready"}

    @router.post("/api/webhooks/unisender-go")
    async def unisender_go_webhook(request: Request):
        if not settings.unisender_webhook_secret:
            raise HTTPException(
                status_code=503,
                detail="Webhook UniSender Go отключён: не настроен обязательный секрет.",
            )
        provided_secret = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret") or ""
        if not secrets.compare_digest(provided_secret, settings.unisender_webhook_secret):
            raise HTTPException(status_code=401, detail="Некорректный секрет webhook UniSender Go")
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Некорректный JSON webhook UniSender Go") from exc
        try:
            result = append_unisender_go_events(payload)
        except Exception as exc:
            logger.exception("unisender_go_webhook_save_failed")
            raise HTTPException(status_code=500, detail=f"Не удалось сохранить webhook UniSender Go: {exc}") from exc
        return {"status": "ok", "result": result}

    @router.post("/api/sender/stop")
    async def sender_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
        result = request_sender_stop(job_id=job_id)
        return {"status": "ok", "result": compact_sender_status(result)}

    @router.post("/api/sender/preview")
    async def sender_preview(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        limit = parse_optional_limit(payload)
        job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
        result = preview_recipients(limit=limit, job_id=job_id)
        return {"status": "ok", "result": result}

    @router.post("/api/sender/chat")
    async def sender_chat(payload: dict = Body(...), username: str = Depends(check_auth)):
        message = str(payload.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = str(payload.get("job_id") or "").strip() or None
        return {"status": "ok", **chat_with_sender(message, job_id=job_id)}

    return router
