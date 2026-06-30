from __future__ import annotations

import json
import secrets
import threading
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.jobs.audit import append_audit_event
from src.web.errors import internal_server_error
from src.web.request_models import ChatRequest, JobScopedRequest, LimitRequest, SenderRunRequest
from src.web.responses import ok_response


WEBHOOK_DEFAULT_MAX_BODY_BYTES = 256 * 1024


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
    append_mailopost_events: Callable[[dict], dict],
    logger: Any,
    request_sender_stop: Callable[..., dict],
    preview_recipients: Callable[..., dict],
    chat_with_sender: Callable[..., dict[str, str]],
    is_load_test_job: Callable[[str | None], bool],
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def webhook_max_body_bytes() -> int:
        try:
            configured = int(getattr(settings, "webhook_max_body_bytes", WEBHOOK_DEFAULT_MAX_BODY_BYTES) or 0)
        except (TypeError, ValueError):
            configured = WEBHOOK_DEFAULT_MAX_BODY_BYTES
        return configured if configured > 0 else WEBHOOK_DEFAULT_MAX_BODY_BYTES

    async def read_webhook_json(request: Request, provider_name: str) -> Any:
        max_body_bytes = webhook_max_body_bytes()
        content_length = str(request.headers.get("content-length") or "").strip()
        if content_length:
            try:
                if int(content_length) > max_body_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Тело webhook {provider_name} превышает лимит {max_body_bytes} байт.",
                    )
            except ValueError:
                pass

        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_body_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Тело webhook {provider_name} превышает лимит {max_body_bytes} байт.",
                )
            chunks.append(chunk)

        raw_body = b"".join(chunks).strip()
        if not raw_body:
            return {}
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"Некорректный JSON webhook {provider_name}") from exc

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

    def resolve_mailopost_webhook_token() -> str:
        return str(
            getattr(settings, "mailopost_webhook_token", "")
            or getattr(settings, "mailopost_webhook_secret", "")
            or ""
        ).strip()

    def ensure_mailopost_webhook_token(token: str) -> None:
        expected_token = resolve_mailopost_webhook_token()
        if not expected_token:
            raise HTTPException(
                status_code=503,
                detail="Webhook MailoPost отключён: не настроен токен webhook.",
            )
        if not secrets.compare_digest(str(token or "").strip(), expected_token):
            raise HTTPException(status_code=401, detail="Некорректный токен webhook MailoPost")
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

    def _schedule_delivery_fallbacks(result: Any, *, provider: str) -> None:
        if not isinstance(result, dict):
            return
        jobs = result.get("jobs")
        if not jobs:
            return
        try:
            from src.generator.delivery.sender_agent import schedule_delivery_fallback_check

            schedule_delivery_fallback_check(jobs, provider=provider)
        except Exception:
            logger.exception("delivery_fallback_schedule_failed", provider=provider, jobs=jobs)

    @router.post("/api/sender/run")
    async def sender_run(payload: SenderRunRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        payload = payload or SenderRunRequest()
        dry_run = payload.dry_run
        limit = payload.limit
        transport = payload.transport
        send_mode = payload.send_mode
        recipient_strategy = payload.recipient_strategy
        subject_template = payload.mail_subject
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        generator_state = get_generator_status(job_id)
        work_type = str(payload.work_type or generator_state.get("work_type") or "").strip() or None
        attachment_mode = _attachment_mode_from_documents(job_id, payload.attachment_mode)
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
                    prime_sender_checking_state(job_id, transport, attachment_mode, recipient_strategy)
                    if dry_run
                    else prime_sender_running_state(job_id, transport, attachment_mode, recipient_strategy)
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
                    "recipient_strategy": recipient_strategy,
                    "subject_template": subject_template,
                    "work_type": work_type,
                    "job_id": job_id,
                },
                name=f"sender-{sender_job_key(job_id)}",
                before_start=_prime_state,
            )
            if not started:
                return {"status": "ok", "result": compact_sender_status(get_sender_status(job_id))}
            append_audit_event(
                action="sender.run",
                principal=principal,
                job_id=job_id,
                details={
                    "dry_run": dry_run,
                    "transport": transport,
                    "send_mode": send_mode,
                    "attachment_mode": attachment_mode,
                    "recipient_strategy": recipient_strategy,
                },
            )
            primed_state = primed_state_box.get("state") or get_sender_status(job_id)
        except Exception as exc:
            logger.exception("sender_run_start_failed", job_id=job_id, transport=transport)
            raise internal_server_error("Не удалось запустить отправку.") from exc
        return {"status": "ok", "result": compact_sender_status(primed_state)}

    @router.get("/api/sender/status")
    async def sender_status(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {"status": "ok", "result": compact_sender_status(get_sender_status(job_id))}

    @router.get("/api/sender/unisender-history")
    async def sender_unisender_history(
        job_id: str | None = None,
        limit: int = 50,
        refresh: bool = False,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {
            "status": "ok",
            "result": get_unisender_history(job_id=job_id, limit=limit, refresh=refresh),
        }

    @router.get("/api/sender/analytics")
    async def sender_analytics(
        job_id: str | None = None,
        refresh: bool = False,
        refresh_wait: bool = False,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
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
        result = {
            "message": "UniSender Go webhook endpoint is ready",
            "token_required": token_configured,
            "url_format": "/api/webhooks/unisender-go/{token}",
            "max_body_bytes": webhook_max_body_bytes(),
        }
        return ok_response(result, **result)

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
        result = {"message": "UniSender Go token webhook endpoint is ready"}
        return ok_response(result, **result)

    @router.post("/api/webhooks/unisender-go/{token}")
    async def unisender_go_webhook_tokenized(token: str, request: Request):
        ensure_webhook_token(token)
        payload = await read_webhook_json(request, "UniSender Go")
        try:
            result = append_unisender_go_events(payload)
            _schedule_delivery_fallbacks(result, provider="unisender")
        except Exception as exc:
            logger.exception("unisender_go_webhook_save_failed")
            raise internal_server_error("Не удалось сохранить webhook UniSender Go.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/webhooks/rusender")
    async def rusender_webhook_health():
        token_configured = bool(resolve_rusender_webhook_token())
        result = {
            "message": "RuSender webhook endpoint is ready",
            "token_required": token_configured,
            "url_format": "/api/webhooks/rusender/{token}",
            "max_body_bytes": webhook_max_body_bytes(),
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
        return ok_response(result, **result)

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
        result = {"message": "RuSender token webhook endpoint is ready"}
        return ok_response(result, **result)

    @router.post("/api/webhooks/rusender/{token}")
    async def rusender_webhook_tokenized(token: str, request: Request):
        ensure_rusender_webhook_token(token)
        payload = await read_webhook_json(request, "RuSender")
        try:
            result = append_rusender_events(payload)
            _schedule_delivery_fallbacks(result, provider="rusender")
        except Exception as exc:
            logger.exception("rusender_webhook_save_failed")
            raise internal_server_error("Не удалось сохранить webhook RuSender.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/webhooks/mailopost")
    async def mailopost_webhook_health():
        token_configured = bool(resolve_mailopost_webhook_token())
        result = {
            "message": "MailoPost webhook endpoint is ready",
            "token_required": token_configured,
            "url_format": "/api/webhooks/mailopost/{token}",
            "max_body_bytes": webhook_max_body_bytes(),
            "kinds": ["api"],
            "events": [
                "delivered",
                "hard_bounced",
                "soft_bounced",
                "skipped",
                "opened",
                "clicked",
                "unsubscribed",
                "complained",
            ],
        }
        return ok_response(result, **result)

    @router.post("/api/webhooks/mailopost")
    async def mailopost_webhook(request: Request):
        if not resolve_mailopost_webhook_token():
            raise HTTPException(
                status_code=503,
                detail="Webhook MailoPost отключён: не настроен токен webhook.",
            )
        raise HTTPException(
            status_code=401,
            detail="Используйте токенизированный URL webhook MailoPost.",
        )

    @router.get("/api/webhooks/mailopost/{token}")
    async def mailopost_webhook_token_health(token: str):
        ensure_mailopost_webhook_token(token)
        result = {"message": "MailoPost token webhook endpoint is ready"}
        return ok_response(result, **result)

    @router.post("/api/webhooks/mailopost/{token}")
    async def mailopost_webhook_tokenized(token: str, request: Request):
        ensure_mailopost_webhook_token(token)
        payload = await read_webhook_json(request, "MailoPost")
        try:
            result = append_mailopost_events(payload)
            _schedule_delivery_fallbacks(result, provider="mailopost")
        except Exception as exc:
            logger.exception("mailopost_webhook_save_failed")
            raise internal_server_error("Не удалось сохранить webhook MailoPost.") from exc
        return {"status": "ok", "result": result}
    @router.post("/api/sender/stop")
    async def sender_stop(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = request_sender_stop(job_id=job_id)
        append_audit_event(action="sender.stop", principal=principal, job_id=job_id)
        return {"status": "ok", "result": compact_sender_status(result)}

    @router.post("/api/sender/preview")
    async def sender_preview(payload: LimitRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        limit = None if payload is None else payload.limit
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = preview_recipients(limit=limit, job_id=job_id)
        return {"status": "ok", "result": result}

    @router.post("/api/sender/chat")
    async def sender_chat(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = chat_with_sender(message, job_id=job_id)
        return ok_response(result, **result)

    return router
