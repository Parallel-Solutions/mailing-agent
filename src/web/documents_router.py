from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, DOCUMENT_RENDERER_VERSION, normalize_document_mode
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type
from src.jobs import resolve_job_paths
from src.utils.logger import logger


def create_documents_router(
    *,
    check_auth: Callable[..., str],
    prefer_existing_file: Callable[[Path, Path], Path],
    compact_documents_status: Callable[[str | None, str | None], dict],
    get_generator_thread: Callable[[str | None], threading.Thread | None],
    get_philologist_thread: Callable[[str | None], threading.Thread | None],
    prime_philologist_running_state: Callable[[str | None, str | None], dict],
    start_documents_thread_if_absent: Callable[..., tuple[threading.Thread, bool]],
    run_documents_pipeline_background: Callable[..., None],
    documents_job_key: Callable[[str | None], str],
    clear_philologist_stop_request: Callable[[str | None], Any],
    get_generator_status: Callable[[str | None], dict],
    get_philologist_status: Callable[[str | None], dict],
    clear_generator_stop_request: Callable[[str | None], Any],
    save_generator_state: Callable[[dict, str | None], Any],
    prime_generator_state: Callable[..., dict],
    request_generator_stop: Callable[[str | None], dict],
    request_philologist_stop: Callable[[str | None], dict],
    documents_agent_choose_reply: Callable[[str, str | None], dict[str, Any]],
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(check_auth)])

    @router.post("/api/documents/start")
    async def documents_start(payload: dict | None = Body(default=None)):
        job_id = str((payload or {}).get("job_id") or "").strip() or None
        mode = str((payload or {}).get("mode") or "fast").strip().lower() or "fast"
        document_mode = normalize_document_mode((payload or {}).get("document_mode") or DOCUMENT_MODE_BOTH)
        work_type = normalize_work_type((payload or {}).get("work_type") or DEFAULT_WORK_TYPE)
        xlsx_path = prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
        if not xlsx_path.exists():
            raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")

        try:
            compact_documents_status(job_id, document_mode)
            generator_state = get_generator_status(job_id)
            philologist_state = get_philologist_status(job_id)
            generator_thread = get_generator_thread(job_id)
            philologist_thread = get_philologist_thread(job_id)
        except Exception as exc:
            logger.exception("documents_start_state_read_failed", job_id=job_id)
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось прочитать состояние подготовки: {type(exc).__name__}: {exc}",
            ) from exc
        generator_thread_running = str(generator_state.get("status") or "") == "running" and generator_thread is not None
        philologist_thread_running = (
            str(philologist_state.get("status") or "") in {"running", "finalizing"}
            and philologist_thread is not None
        )
        if generator_thread_running or philologist_thread_running:
            return {"status": "ok", "result": compact_documents_status(job_id, document_mode)}

        generator_document_mode = normalize_document_mode(generator_state.get("document_mode") or DOCUMENT_MODE_BOTH)
        generator_work_type = normalize_work_type(generator_state.get("work_type") or DEFAULT_WORK_TYPE)
        generator_renderer_version = str(generator_state.get("renderer_version") or "")
        generator_is_current = (
            generator_document_mode == document_mode
            and generator_work_type == work_type
            and generator_renderer_version == DOCUMENT_RENDERER_VERSION
        )
        if str(generator_state.get("status") or "") == "completed":
            if generator_is_current and str(philologist_state.get("status") or "") != "completed":
                clear_philologist_stop_request(job_id)
                prime_philologist_running_state(job_id, mode)
            elif not generator_is_current:
                try:
                    primed_state = prime_generator_state(xlsx_path=xlsx_path, job_id=job_id, document_mode=document_mode, work_type=work_type)
                except Exception as exc:
                    logger.exception("documents_start_reprime_generator_failed", job_id=job_id, xlsx_path=str(xlsx_path))
                    raise HTTPException(
                        status_code=500,
                        detail=f"Не удалось подготовить новый режим документов: {type(exc).__name__}: {exc}",
                    ) from exc
                if primed_state.get("status") == "error":
                    raise HTTPException(
                        status_code=400,
                        detail=primed_state.get("summary_text") or "Ошибка подготовки документов",
                    )
        else:
            if str(generator_state.get("status") or "") == "stopped" and generator_is_current:
                clear_generator_stop_request(job_id)
                generator_state["status"] = "running"
                generator_state["completed_at"] = None
                generator_state["summary_text"] = "Продолжаю подготовку документов с сохраненного места."
                save_generator_state(generator_state, job_id)
            else:
                try:
                    primed_state = prime_generator_state(xlsx_path=xlsx_path, job_id=job_id, document_mode=document_mode, work_type=work_type)
                except Exception as exc:
                    logger.exception("documents_start_prime_generator_failed", job_id=job_id, xlsx_path=str(xlsx_path))
                    raise HTTPException(
                        status_code=500,
                        detail=f"Не удалось подготовить запуск документов: {type(exc).__name__}: {exc}",
                    ) from exc
                if primed_state.get("status") == "error":
                    raise HTTPException(
                        status_code=400,
                        detail=primed_state.get("summary_text") or "Ошибка подготовки документов",
                    )

        try:
            start_documents_thread_if_absent(
                job_id,
                target=run_documents_pipeline_background,
                kwargs={"xlsx_path": xlsx_path, "job_id": job_id, "mode": mode, "document_mode": document_mode, "work_type": work_type},
                name=f"documents-{documents_job_key(job_id)}",
            )
            result = compact_documents_status(job_id, document_mode)
        except Exception as exc:
            logger.exception("documents_start_thread_failed", job_id=job_id, xlsx_path=str(xlsx_path))
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось запустить подготовку документов: {type(exc).__name__}: {exc}",
            ) from exc
        return {"status": "ok", "result": result}

    @router.get("/api/documents/status")
    async def documents_status(job_id: str | None = None, document_mode: str | None = None):
        return {"status": "ok", "result": compact_documents_status(job_id, document_mode)}

    @router.post("/api/documents/stop")
    async def documents_stop(payload: dict | None = Body(default=None)):
        job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
        generator_state = get_generator_status(job_id)
        philologist_state = get_philologist_status(job_id)
        if str(generator_state.get("status") or "") == "running":
            request_generator_stop(job_id)
        if str(philologist_state.get("status") or "") != "completed":
            request_philologist_stop(job_id)
        return {"status": "ok", "result": compact_documents_status(job_id)}

    @router.post("/api/documents/chat")
    async def documents_chat(payload: dict = Body(...)):
        message = str(payload.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = str(payload.get("job_id") or "").strip() or None
        return {"status": "ok", **documents_agent_choose_reply(message, job_id=job_id)}

    return router
