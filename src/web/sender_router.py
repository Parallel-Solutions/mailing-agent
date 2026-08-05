from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.security.auth import coerce_principal
from src.web.errors import internal_server_error
from src.web.request_models import ChatRequest, JobScopedRequest, LimitRequest, SenderRunRequest
from src.web.responses import ok_response
from src.web.consent_sales_service import build_sales_consent_requests


WEBHOOK_DEFAULT_MAX_BODY_BYTES = 256 * 1024


def create_sender_router(
    *,
    check_auth: Callable[..., str],
    parse_optional_limit: Callable[[dict | None], int | None],
    compact_sender_status: Callable[[dict], dict],
    clear_sender_stop_request: Callable[[str | None], Any],
    prime_sender_checking_state: Callable[..., dict],
    prime_sender_running_state: Callable[..., dict],
    prime_sender_queued_state: Callable[..., dict],
    prime_sender_scheduled_state: Callable[..., dict],
    start_sender_thread_if_absent: Callable[..., tuple[Any, bool]],
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
        try:
            from src.campaigns.delivery_fallback_service import schedule_campaign_delivery_fallbacks_from_webhook_result

            schedule_campaign_delivery_fallbacks_from_webhook_result(result, provider=provider)
        except Exception:
            logger.exception("campaign_delivery_fallback_schedule_failed", provider=provider, jobs=jobs)

    @router.post("/api/sender/run")
    def sender_run(payload: SenderRunRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        # Legacy xlsx-рассылка (sender_agent.run_sender) отключена: у неё нет UI,
        # нет учёта открытий/кликов и она была доступна без ограничения по ролям.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.get("/api/sender/queue")
    async def sender_queue(job_id: str | None = None, principal: object = Depends(check_auth)):
        # Legacy xlsx-очередь (task_type="sender") отключена вместе с /api/sender/run:
        # CampaignFlow использует task_type="sender_batch"/"chain_followup", эта очередь
        # больше никогда не наполняется.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.post("/api/sender/scheduled/cancel")
    def sender_scheduled_cancel(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        # Legacy xlsx-рассылка отключена вместе с /api/sender/run — планировать
        # отложенный старт для неё больше нельзя, отменять нечего.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.post("/api/sender/resume")
    async def sender_resume(principal: object = Depends(check_auth)):
        from src.jobs.access import coerce_principal
        from src.generator.delivery.send_guard import get_send_guard_status, resume_sending

        actor = coerce_principal(principal)
        if not actor.is_admin:
            raise HTTPException(status_code=403, detail="Только администратор может возобновить отправку.")
        resume_sending()
        return {"status": "ok", "result": get_send_guard_status()}

    @router.get("/api/sender/suppression")
    async def sender_suppression_list(
        limit: int = 200,
        offset: int = 0,
        q: str = "",
        principal: object = Depends(check_auth),
    ):
        from src.generator.delivery.suppression_store import list_suppressions

        return {"status": "ok", "result": list_suppressions(limit=limit, offset=offset, q=q)}

    @router.post("/api/sender/suppression")
    async def sender_suppression_add(payload: dict = Body(default={}), principal: object = Depends(check_auth)):
        from src.jobs.access import coerce_principal
        from src.generator.delivery.suppression_store import upsert_suppression

        actor = coerce_principal(principal)
        if not actor.is_admin:
            raise HTTPException(status_code=403, detail="Только администратор может редактировать стоп-лист.")
        email = str(payload.get("email") or "").strip()
        reason = str(payload.get("reason") or "manual").strip()
        if not email:
            raise HTTPException(status_code=400, detail="email обязателен")
        if reason == "unsubscribe":
            from src.campaigns.suppression_service import apply_global_email_suppression

            result = apply_global_email_suppression(email, reason=reason, source="manual")
            return {"status": "ok", "result": {"email": email, "reason": reason, **result}}
        upsert_suppression(email, reason=reason, source="manual")
        return {"status": "ok", "result": {"email": email, "reason": reason}}

    @router.delete("/api/sender/suppression")
    async def sender_suppression_remove(email: str = "", principal: object = Depends(check_auth)):
        from src.jobs.access import coerce_principal
        from src.generator.delivery.suppression_store import remove_suppression

        actor = coerce_principal(principal)
        if not actor.is_admin:
            raise HTTPException(status_code=403, detail="Только администратор может редактировать стоп-лист.")
        if not str(email or "").strip():
            raise HTTPException(status_code=400, detail="email обязателен")
        removed = remove_suppression(email)
        return {"status": "ok", "result": {"removed": removed, "email": email}}

    @router.get("/api/sender/domain-stats")
    async def sender_domain_stats(principal: object = Depends(check_auth)):
        # domain_rate_limiter обслуживает только legacy xlsx-агента (sender_agent.py);
        # CampaignFlow (batch_worker.py/chain_send_service.py) его не вызывает.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.get("/api/sender/webhook-status")
    async def sender_webhook_status(principal: object = Depends(check_auth)):
        # Диагностика поверх legacy-модели sent_mail_log/*_unmatched.jsonl.
        # Отключена вместе с остальным legacy xlsx-потоком.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.get("/api/sender/status")
    def sender_status(job_id: str | None = None, principal: object = Depends(check_auth)):
        # Читает legacy SENDER_STATE (sender_agent.py) — отключено вместе с /api/sender/run.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.get("/api/sender/unisender-history")
    def sender_unisender_history(
        job_id: str | None = None,
        limit: int = 50,
        refresh: bool = False,
        principal: object = Depends(check_auth),
    ):
        # Legacy job state (sender_agent.py) — отключено вместе с /api/sender/run.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.get("/api/sender/analytics")
    def sender_analytics(
        job_id: str | None = None,
        refresh: bool = False,
        refresh_wait: bool = False,
        principal: object = Depends(check_auth),
    ):
        # Legacy job state (sender_agent.py) — отключено вместе с /api/sender/run.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.get("/api/consents/sales-requests")
    def consent_sales_requests(
        job_id: str | None = None,
        include_all: bool = False,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        try:
            result = build_sales_consent_requests(job_id, include_all=include_all)
        except Exception as exc:
            logger.exception("consent_sales_requests_failed", job_id=job_id)
            raise internal_server_error("Не удалось получить заявки по согласиям.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/webhooks/unisender-go")
    def unisender_go_webhook_health():
        token_configured = bool(resolve_webhook_token())
        result = {
            "message": "UniSender Go webhook endpoint is ready",
            "token_required": token_configured,
            "url_format": "/api/webhooks/unisender-go/{token}",
            "max_body_bytes": webhook_max_body_bytes(),
        }
        return ok_response(result, **result)

    @router.post("/api/webhooks/unisender-go")
    def unisender_go_webhook(request: Request):
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
    def unisender_go_webhook_token_health(token: str):
        ensure_webhook_token(token)
        result = {"message": "UniSender Go token webhook endpoint is ready"}
        return ok_response(result, **result)

    @router.post("/api/webhooks/unisender-go/{token}")
    async def unisender_go_webhook_tokenized(token: str, request: Request):
        ensure_webhook_token(token)
        payload = await read_webhook_json(request, "UniSender Go")
        try:
            result = await run_in_threadpool(append_unisender_go_events, payload)
            await run_in_threadpool(_schedule_delivery_fallbacks, result, provider="unisender")
        except Exception as exc:
            logger.exception("unisender_go_webhook_save_failed")
            raise internal_server_error("Не удалось сохранить webhook UniSender Go.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/webhooks/rusender")
    def rusender_webhook_health():
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
    def rusender_webhook(request: Request):
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
    def rusender_webhook_token_health(token: str):
        ensure_rusender_webhook_token(token)
        result = {"message": "RuSender token webhook endpoint is ready"}
        return ok_response(result, **result)

    @router.post("/api/webhooks/rusender/{token}")
    async def rusender_webhook_tokenized(token: str, request: Request):
        ensure_rusender_webhook_token(token)
        payload = await read_webhook_json(request, "RuSender")
        try:
            result = await run_in_threadpool(append_rusender_events, payload)
            await run_in_threadpool(_schedule_delivery_fallbacks, result, provider="rusender")
        except Exception as exc:
            logger.exception("rusender_webhook_save_failed")
            raise internal_server_error("Не удалось сохранить webhook RuSender.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/webhooks/mailopost")
    def mailopost_webhook_health():
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
    def mailopost_webhook(request: Request):
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
    def mailopost_webhook_token_health(token: str):
        ensure_mailopost_webhook_token(token)
        result = {"message": "MailoPost token webhook endpoint is ready"}
        return ok_response(result, **result)

    @router.post("/api/webhooks/mailopost/{token}")
    async def mailopost_webhook_tokenized(token: str, request: Request):
        ensure_mailopost_webhook_token(token)
        payload = await read_webhook_json(request, "MailoPost")
        try:
            result = await run_in_threadpool(append_mailopost_events, payload)
            await run_in_threadpool(_schedule_delivery_fallbacks, result, provider="mailopost")
        except Exception as exc:
            logger.exception("mailopost_webhook_save_failed")
            raise internal_server_error("Не удалось сохранить webhook MailoPost.") from exc
        return {"status": "ok", "result": result}
    @router.post("/api/sender/stop")
    def sender_stop(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        # Legacy SENDER_STATE (sender_agent.py) — отключено вместе с /api/sender/run.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.post("/api/sender/preview")
    def sender_preview(payload: LimitRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        # Legacy preview_recipients (sender_agent.py) — отключено вместе с /api/sender/run.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    @router.post("/api/sender/chat")
    def sender_chat(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        # Legacy chat_with_sender (sender_agent.py) — отключено вместе с /api/sender/run.
        raise HTTPException(status_code=404, detail="Эндпоинт отключён в этой ветке.")

    return router
