from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.jobs.audit import append_audit_event
from src.parser.agent import chat, clear_memory, get_memory, set_system_prompt
from src.web.request_models import ChatRequest, JobScopedRequest, LimitRequest, PromptRequest


def create_parser_router(
    *,
    check_auth: Callable[..., Any],
    parse_optional_limit: Callable[[dict | None], int | None],
    start_parser_thread_if_absent: Callable[..., tuple[Any, bool]],
    parser_job_key: Callable[[str | None], str],
    get_parser_thread: Callable[[str | None], Any],
    run_parser_agent: Callable[..., dict],
    get_parser_status: Callable[..., dict],
    run_parser_municipality_verification: Callable[..., dict],
    format_municipality_verification_for_chat: Callable[..., str],
    parser_progress_subscribe: Callable[[str], Any],
    logger: Any,
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post("/api/parser/chat-v2")
    def parser_chat_v2(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        return chat(message, job_id=job_id)

    @router.post("/api/parser/start")
    def parser_start(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        existing_thread = get_parser_thread(job_id)
        if existing_thread is not None:
            return {"status": "ok", "result": get_parser_status(job_id), "accepted": True, "started": False}
        try:
            _, started = start_parser_thread_if_absent(
                job_id,
                task="parser_start",
                kwargs={"job_id": job_id},
                name=f"parser-start-{parser_job_key(job_id)}",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("parser_start_worker_failed", job_id=job_id)
            raise HTTPException(status_code=500, detail="Не удалось запустить парсер в фоне.") from exc
        append_audit_event(action="parser.start", principal=principal, job_id=job_id)
        return {"status": "ok", "result": get_parser_status(job_id), "accepted": True, "started": started}

    @router.get("/api/parser/memory")
    def parser_memory(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        return get_memory(job_id=job_id)

    @router.post("/api/parser/memory/clear")
    def parser_memory_clear(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        clear_memory(job_id=job_id)
        return {"status": "ok"}

    @router.post("/api/parser/prompt")
    def parser_prompt(payload: PromptRequest = Body(...), principal: object = Depends(check_auth)):
        prompt = payload.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Пустой промпт")
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        set_system_prompt(prompt, job_id=job_id)
        return {"status": "ok"}

    @router.post("/api/parser/run")
    def parser_run(payload: LimitRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        limit = None if payload is None else payload.limit
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        existing_thread = get_parser_thread(job_id)
        if existing_thread is not None:
            return {"status": "ok", "result": get_parser_status(job_id), "accepted": True, "started": False}
        try:
            _, started = start_parser_thread_if_absent(
                job_id,
                task="parser_agent",
                kwargs={"job_id": job_id, "limit": limit},
                name=f"parser-agent-{parser_job_key(job_id)}",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("parser_run_worker_failed", job_id=job_id)
            raise HTTPException(status_code=500, detail="Не удалось запустить агента-парсера в фоне.") from exc
        append_audit_event(action="parser.run", principal=principal, job_id=job_id, details={"limit": limit})
        return {"status": "ok", "result": get_parser_status(job_id), "accepted": True, "started": started}

    @router.get("/api/parser/status")
    def parser_status(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {"status": "ok", "result": get_parser_status(job_id)}

    @router.post("/api/parser/merge-rmz")
    def merge_rmz(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        from src.parser.rmz_merger import run_merge

        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = run_merge(job_id=job_id)
        if result.suspicious:
            suspicious_list = [
                {
                    "mo_name": item.mo_name,
                    "org_name": item.org_name,
                    "sub_rf": item.sub_rf,
                    "mun_r_name": item.mun_r_name,
                    "reason": item.reason,
                }
                for item in result.suspicious
            ]
            agent_reply = chat(
                f"Из {len(suspicious_list)} спорных совпадений коротко скажи сколько верных и сколько неверных. "
                f"Только цифры, без перечисления. Данные: {suspicious_list}",
                job_id=job_id,
            )
            return {
                "written": result.written,
                "skipped_existing": result.skipped_existing,
                "not_found": result.not_found,
                "suspicious_count": len(result.suspicious),
                "agent_review": agent_reply.get("reply", ""),
            }
        return {
            "written": result.written,
            "skipped_existing": result.skipped_existing,
            "not_found": result.not_found,
            "suspicious_count": 0,
        }

    @router.post("/api/parser/chat")
    async def parser_chat(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = payload.job_id
        await run_in_threadpool(ensure_job_access, job_id, principal, allow_missing=True)

        result = await run_in_threadpool(chat, message, job_id=job_id)
        result_file = result.get("result_file")
        if result_file:
            try:
                from src.generator.verification.municipality_name_verifier import (
                    verify_municipality_names_in_workbook,
                )

                verification = await run_in_threadpool(verify_municipality_names_in_workbook, Path(result_file))
                result["municipality_name_verification"] = verification
                summary = format_municipality_verification_for_chat(verification, max_samples=20)
                if summary:
                    logger.info(f"[parser] Верификация имён МО: {summary}")
            except Exception as exc:
                logger.warning(f"Верификация имён МО не выполнена: {exc}")

        return {"status": "ok", **result}
    
    @router.post("/api/parser/topup")
    def parser_topup(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = payload.job_id
        if not job_id:
            raise HTTPException(status_code=400, detail="Для дозаполнения нужен файл (job_id)")
        ensure_job_access(job_id, principal, allow_missing=True)

        from src.parser.agent import topup
        result = topup(message, job_id)

        # тот же блок верификации имён МО, что и в /chat — чтобы поведение совпадало
        result_file = result.get("result_file")
        if result_file:
            try:
                from src.generator.verification.municipality_name_verifier import (
                    verify_municipality_names_in_workbook,
                )
                verification = verify_municipality_names_in_workbook(Path(result_file))
                result["municipality_name_verification"] = verification
                summary = format_municipality_verification_for_chat(verification, max_samples=20)
                if summary:
                    logger.info(f"[parser] Верификация имён МО (topup): {summary}")
            except Exception as exc:
                logger.warning(f"Верификация имён МО не выполнена: {exc}")

        return {"status": "ok", **result}

    @router.post("/api/parser/fill")
    def parser_fill(payload: dict | None = Body(default=None), principal: object = Depends(check_auth)):
        payload = payload or {}
        job_id = payload.get("job_id")
        verify_emails = bool(payload.get("verify_emails"))
        if not job_id:
            raise HTTPException(status_code=400, detail="Для заполнения нужен файл (job_id)")
        ensure_job_access(job_id, principal, allow_missing=True)

        from src.parser.agent import fill_gaps
        result = fill_gaps(job_id, verify_emails)
        return {"status": "ok", **result}

    @router.post("/api/parser/cancel")
    def parser_cancel(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        from src.parser_new.progress import request_stop
        request_stop(job_id)
        return {"status": "ok"}

    @router.get("/api/parser/progress")
    def parser_progress(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        job_key = str(job_id or "").strip()
        if not job_key:
            raise HTTPException(status_code=400, detail="Не указан job_id для потока прогресса")
        return StreamingResponse(
            parser_progress_subscribe(job_key),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
