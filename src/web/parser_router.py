from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from src.parser.agent import chat, clear_memory, get_memory, run_batch_parser, set_system_prompt


def _job_id_from_payload(payload: dict | None) -> str | None:
    return None if payload is None else str(payload.get("job_id") or "").strip() or None


def create_parser_router(
    *,
    check_auth: Callable[..., Any],
    parse_optional_limit: Callable[[dict | None], int | None],
    run_parser_agent: Callable[..., dict],
    get_parser_status: Callable[..., dict],
    run_parser_municipality_verification: Callable[..., dict],
    format_municipality_verification_for_chat: Callable[..., str],
    parser_progress_subscribe: Callable[[str], Any],
    logger: Any,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/parser/chat-v2")
    async def parser_chat_v2(payload: dict = Body(...), username: str = Depends(check_auth)):
        message = str(payload.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = str(payload.get("job_id") or "").strip() or None
        return chat(message, job_id=job_id)

    @router.post("/api/parser/start")
    async def parser_start(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        job_id = _job_id_from_payload(payload)
        parser_result = run_batch_parser(job_id=job_id)
        verification_result = {}
        if parser_result.get("status") != "error":
            verification_result = run_parser_municipality_verification(job_id, source="parser")
        verification_summary = format_municipality_verification_for_chat(verification_result, max_samples=20)
        parser_reply = str(parser_result.get("reply") or "").strip()
        summary_parts = [part for part in [verification_summary, parser_reply] if part]
        result = {
            **parser_result,
            "summary_text": "\n\n".join(summary_parts).strip() or "Парсер завершил обработку.",
            "municipality_name_verification": verification_result,
        }
        return {"status": "ok", "result": result}

    @router.get("/api/parser/memory")
    async def parser_memory(job_id: str | None = None, username: str = Depends(check_auth)):
        return get_memory(job_id=job_id)

    @router.post("/api/parser/memory/clear")
    async def parser_memory_clear(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        clear_memory(job_id=_job_id_from_payload(payload))
        return {"status": "ok"}

    @router.post("/api/parser/prompt")
    async def parser_prompt(payload: dict = Body(...), username: str = Depends(check_auth)):
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Пустой промпт")
        job_id = str(payload.get("job_id") or "").strip() or None
        set_system_prompt(prompt, job_id=job_id)
        return {"status": "ok"}

    @router.post("/api/parser/run")
    async def parser_run(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        limit = parse_optional_limit(payload)
        job_id = _job_id_from_payload(payload)
        result = run_parser_agent(limit=limit, job_id=job_id)
        return {"status": "ok", "result": result}

    @router.get("/api/parser/status")
    async def parser_status(job_id: str | None = None, username: str = Depends(check_auth)):
        return {"status": "ok", "result": get_parser_status(job_id)}

    @router.post("/api/parser/merge-rmz")
    async def merge_rmz(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
        from src.parser.rmz_merger import run_merge

        job_id = _job_id_from_payload(payload)
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
    async def parser_chat(payload: dict = Body(...), username: str = Depends(check_auth)):
        message = str(payload.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = str(payload.get("job_id") or "").strip() or None

        result = await run_in_threadpool(chat, message, job_id=job_id)
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
                    logger.info(f"[parser] Верификация имён МО: {summary}")
            except Exception as exc:
                logger.warning(f"Верификация имён МО не выполнена: {exc}")

        return {"status": "ok", **result}

    @router.get("/api/parser/progress")
    async def parser_progress(job_id: str | None = None, username: str = Depends(check_auth)):
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
