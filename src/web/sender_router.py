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
    clear_sender_stop_request: Callable[[str | None], Any],
    prime_sender_checking_state: Callable[[str | None, str | None, str | None], dict],
    prime_sender_running_state: Callable[[str | None, str | None, str | None], dict],
    start_sender_thread_if_absent: Callable[..., tuple[threading.Thread, bool]],
    run_sender_background: Callable[..., None],
    sender_job_key: Callable[[str | None], str],
    get_sender_status: Callable[[str | None], dict],
    get_generator_status: Callable[[str | None], dict],
    get_unisender_history: Callable[..., dict],
    build_sender_delivery_analytics: Callable[..., dict],
    settings: Any,
    append_unisender_go_events: Callable[[dict], dict],
    append_rusender_events: Callable[[dict], dict],
    logger: Any,
    request_sender_stop: Callable[..., dict],
    preview_recipients: Callable[..., dict],
    chat_with_sender: Callable[..., dict[str, str]],
    is_load_test_job: Callable[[str | None], bool],
) -> APIRouter:
    router = APIRouter()

    def resolve_webhook_token() -> str:
        return str(
            getattr(settings, "unisender_webhook_token", "")
            or getattr(settings, "unisender_webhook_secret", "")
            or ""
        ).strip()

    def ensure_webhook_token(token: str) -> None:
        expected_token = resolve_webhook_token()
        if not expected_token:
            raise HTTPException(
                status_code=503,
                detail="Webhook UniSender Go отключён: не настроен токен webhook.",
            )
        if not secrets.compare_digest(str(token or "").strip(), expected_token):
            raise HTTPException(status_code=401, detail="Некорректный токен webhook UniSender Go")

    def resolve_rusender_webhook_token() -> str:
        return str(
            getattr(settings, "rusender_webhook_token", "")
            or getattr(settings, "rusender_webhook_secret", "")
            or ""
        ).strip()

    def ensure_rusender_webhook_token(token: str) -> None:
        expected_token = resolve_rusender_webhook_token()
        if not expected_token:
            raise HTTPException(
                status_code=503,
                detail="Webhook RuSender отключён: не настроен токен webhook.",
            )
        if not secrets.compare_digest(str(token or "").strip(), expected_token):
            raise HTTPException(status_code=401, detail="Некорректный токен webhook RuSender")

    def _attachment_mode_from_documents(job_id: str | None, fallback: object = None) -> str:
        generator_state = get_generator_status(job_id)
        document_mode = str(generator_state.get("document_mode") or "").strip().lower()
        if document_mode in {"kp", "contract", "both"}:
            return document_mode
        fallback_mode = str(fallback or "").strip().lower()
        if fallback_mode in {"kp", "contract", "both"}:
            return fallback_mode
        sender_state = get_sender_status(job_id)
        sender_mode = str(sender_state.get("attachment_mode") or "").strip().lower()
        return sender_mode if sender_mode in {"kp", "contract", "both"} else "kp"

    @router.post("/api/sender/run")
    async def sender_run(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        dry_run = True if payload is None else bool(payload.get("dry_run", True))
        limit = parse_optional_limit(payload)
        transport = None if payload is None else payload.get("transport")
        send_mode = None if payload is None else payload.get("send_mode")
        subject_template = None if payload is None else payload.get("mail_subject")
        job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
        generator_state = get_generator_status(job_id)
        work_type = str((payload or {}).get("work_type") or generator_state.get("work_type") or "").strip() or None
        attachment_mode = _attachment_mode_from_documents(
            job_id,
            None if payload is None else payload.get("attachment_mode"),
        )
        if not dry_run and is_load_test_job(job_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Это нагрузочный тест. Реальная отправка для тестовых job запрещена: "
                    "можно только проверить письма без отправки."
                ),
            )

        try:
            clear_sender_stop_request(job_id)
            primed_state_box: dict[str, Any] = {}

            def _prime_state() -> None:
                primed_state_box["state"] = (
                    prime_sender_checking_state(job_id, transport, attachment_mode)
                    if dry_run
                    else prime_sender_running_state(job_id, transport, attachment_mode)
                )

            _, started = start_sender_thread_if_absent(
                job_id,
                target=run_sender_background,
                kwargs={
                    "dry_run": dry_run,
                    "limit": limit,
                    "transport": transport,
                    "send_mode": send_mode,
                    "attachment_mode": attachment_mode,
                    "subject_template": subject_template,
                    "work_type": work_type,
                    "job_id": job_id,
                },
                name=f"sender-{sender_job_key(job_id)}",
                before_start=_prime_state,
            )
            if not started:
                return {"status": "ok", "result": compact_sender_status(get_sender_status(job_id))}
            primed_state = primed_state_box.get("state") or get_sender_status(job_id)
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
        refresh_wait: bool = False,
        username: str = Depends(check_auth),
    ):
        return {
            "status": "ok",
            "result": build_sender_delivery_analytics(
                job_id=job_id,
                refresh=refresh,
                refresh_wait=refresh_wait,
            ),
        }

    @router.get("/api/webhooks/unisender-go")
    async def unisender_go_webhook_health():
        token_configured = bool(resolve_webhook_token())
        return {
            "status": "ok",
            "message": "UniSender Go webhook endpoint is ready",
            "token_required": token_configured,
            "url_format": "/api/webhooks/unisender-go/{token}",
        }

    @router.post("/api/webhooks/unisender-go")
    async def unisender_go_webhook(request: Request):
        if not resolve_webhook_token():
            raise HTTPException(
                status_code=503,
                detail="Webhook UniSender Go отключён: не настроен токен webhook.",
            )
        raise HTTPException(
            status_code=401,
            detail="Используйте токенизированный URL webhook UniSender Go.",
        )

    @router.get("/api/webhooks/unisender-go/{token}")
    async def unisender_go_webhook_token_health(token: str):
        ensure_webhook_token(token)
        return {"status": "ok", "message": "UniSender Go token webhook endpoint is ready"}

    @router.post("/api/webhooks/unisender-go/{token}")
    async def unisender_go_webhook_tokenized(token: str, request: Request):
        ensure_webhook_token(token)
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

    @router.get("/api/webhooks/rusender")
    async def rusender_webhook_health():
        token_configured = bool(resolve_rusender_webhook_token())
        return {
            "status": "ok",
            "message": "RuSender webhook endpoint is ready",
            "token_required": token_configured,
            "url_format": "/api/webhooks/rusender/{token}",
            "events": [
                "external_mail.delivered",
                "external_mail.hard_bounced",
                "external_mail.soft_bounced",
                "external_mail.error",
                "external_mail.open",
                "external_mail.click",
                "external_mail.unsubscribe",
                "external_mail.complaint",
            ],
        }

    @router.post("/api/webhooks/rusender")
    async def rusender_webhook(request: Request):
        if not resolve_rusender_webhook_token():
            raise HTTPException(
                status_code=503,
                detail="Webhook RuSender отключён: не настроен токен webhook.",
            )
        raise HTTPException(
            status_code=401,
            detail="Используйте токенизированный URL webhook RuSender.",
        )

    @router.get("/api/webhooks/rusender/{token}")
    async def rusender_webhook_token_health(token: str):
        ensure_rusender_webhook_token(token)
        return {"status": "ok", "message": "RuSender token webhook endpoint is ready"}

    @router.post("/api/webhooks/rusender/{token}")
    async def rusender_webhook_tokenized(token: str, request: Request):
        ensure_rusender_webhook_token(token)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Некорректный JSON webhook RuSender") from exc
        try:
            result = append_rusender_events(payload)
        except Exception as exc:
            logger.exception("rusender_webhook_save_failed")
            raise HTTPException(status_code=500, detail=f"Не удалось сохранить webhook RuSender: {exc}") from exc
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
