from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import quote, urlencode
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, DOCUMENT_RENDERER_VERSION, normalize_document_mode
from src.generator.generation.template_analysis import build_template_analysis_context, save_template_analysis_context
from src.generator.generation.template_preview import build_template_preview, is_template_preview_approved, resolve_template_preview_file
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type
from src.jobs import resolve_job_paths
from src.jobs.access import JobAccessDenied, authorize_job_access
from src.jobs.audit import append_audit_event
from src.utils.logger import logger
from src.web.errors import internal_server_error
from src.web.request_models import ChatRequest, DocumentsStartRequest, JobScopedRequest, TemplatePreviewRequest
from src.web.responses import ok_response


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
    documents_agent_choose_reply: Callable[..., dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post("/api/documents/start")
    async def documents_start(payload: DocumentsStartRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        payload = payload or DocumentsStartRequest()
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        mode = str(payload.mode or "fast").strip().lower() or "fast"
        document_mode = normalize_document_mode(payload.document_mode or DOCUMENT_MODE_BOTH)
        work_type = normalize_work_type(payload.work_type or DEFAULT_WORK_TYPE)
        job_paths = resolve_job_paths(job_id)
        from src.jobs.clients_store import prepare_data_xlsx

        xlsx_path = prepare_data_xlsx(job_id, job_paths.data_xlsx)
        if not xlsx_path.exists():
            raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")

        try:
            initial_documents_status = compact_documents_status(job_id, document_mode)
            generator_state = get_generator_status(job_id)
            philologist_state = get_philologist_status(job_id)
            generator_thread = get_generator_thread(job_id)
            philologist_thread = get_philologist_thread(job_id)
        except Exception as exc:
            logger.exception("documents_start_state_read_failed", job_id=job_id)
            raise internal_server_error("Не удалось прочитать состояние подготовки.") from exc
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
        is_resume_after_generator = generator_is_current and str(generator_state.get("status") or "") == "completed" and str(philologist_state.get("status") or "") == "stopped"
        is_resume_after_stop = generator_is_current and str(generator_state.get("status") or "") == "stopped"
        if generator_is_current and bool(initial_documents_status.get("restart_locked")):
            raise HTTPException(
                status_code=409,
                detail="Документы уже успешно подготовлены без ошибок. Повторный запуск для этой сессии заблокирован.",
            )
        if not (is_resume_after_generator or is_resume_after_stop) and not is_template_preview_approved(
            job_id,
            document_mode=document_mode,
            work_type=work_type,
        ):
            raise HTTPException(
                status_code=409,
                detail="Сначала соберите пример документа в чате и подтвердите, что шаблон выглядит правильно.",
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
                    raise internal_server_error("Не удалось подготовить новый режим документов.") from exc
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
                    raise internal_server_error("Не удалось подготовить запуск документов.") from exc
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
            append_audit_event(
                action="documents.start",
                principal=principal,
                job_id=job_id,
                details={"mode": mode, "document_mode": document_mode, "work_type": work_type},
            )
        except Exception as exc:
            logger.exception("documents_start_thread_failed", job_id=job_id, xlsx_path=str(xlsx_path))
            raise internal_server_error("Не удалось запустить подготовку документов.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/documents/status")
    async def documents_status(
        job_id: str | None = None,
        document_mode: str | None = None,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        return {"status": "ok", "result": compact_documents_status(job_id, document_mode)}

    @router.get("/api/documents/template-analysis")
    async def documents_template_analysis(
        job_id: str | None = None,
        document_mode: str | None = None,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        result = build_template_analysis_context(job_id=job_id, document_mode=normalize_document_mode(document_mode or DOCUMENT_MODE_BOTH))
        save_template_analysis_context(result, job_id=job_id)
        append_audit_event(action="documents.template_analysis", principal=principal, job_id=job_id)
        return ok_response(result, **result)

    @router.post("/api/documents/template-preview")
    async def documents_template_preview(payload: TemplatePreviewRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        payload = payload or TemplatePreviewRequest()
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        try:
            result = build_template_preview(
                job_id=job_id,
                document_mode=normalize_document_mode(payload.document_mode or DOCUMENT_MODE_BOTH),
                work_type=normalize_work_type(payload.work_type or DEFAULT_WORK_TYPE),
                row_id=payload.row_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("documents_template_preview_failed", job_id=job_id)
            raise internal_server_error("Не удалось собрать пример документа для предпросмотра.") from exc

        url_query = {"kind": "pdf"}
        if job_id:
            url_query["job_id"] = job_id
        if result.get("has_pdf"):
            result["pdf_url"] = "/api/documents/template-preview/file?" + urlencode(url_query)
        url_query["kind"] = "docx"
        if result.get("has_docx"):
            result["docx_url"] = "/api/documents/template-preview/file?" + urlencode(url_query)
        append_audit_event(action="documents.template_preview", principal=principal, job_id=job_id)
        return ok_response(result, **result)

    @router.get("/api/documents/template-preview/file")
    async def documents_template_preview_file(
        job_id: str | None = None,
        kind: str = "pdf",
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        normalized_kind = "docx" if str(kind or "").lower() == "docx" else "pdf"
        try:
            path = resolve_template_preview_file(job_id, normalized_kind)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if normalized_kind == "docx"
            else "application/pdf"
        )
        headers = {
            "Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        }
        if normalized_kind == "pdf":
            encoded_filename = quote(path.name)
            headers["Content-Disposition"] = (
                f"inline; filename=template-preview.pdf; filename*=UTF-8''{encoded_filename}"
            )
            return FileResponse(path, media_type=media_type, headers=headers)
        return FileResponse(path, media_type=media_type, filename=path.name, headers=headers)

    @router.post("/api/documents/stop")
    async def documents_stop(payload: JobScopedRequest | None = Body(default=None), principal: object = Depends(check_auth)):
        job_id = None if payload is None else payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        generator_state = get_generator_status(job_id)
        philologist_state = get_philologist_status(job_id)
        if str(generator_state.get("status") or "") == "running":
            request_generator_stop(job_id)
        if str(philologist_state.get("status") or "") != "completed":
            request_philologist_stop(job_id)
        append_audit_event(action="documents.stop", principal=principal, job_id=job_id)
        return {"status": "ok", "result": compact_documents_status(job_id)}

    @router.post("/api/documents/chat")
    async def documents_chat(payload: ChatRequest = Body(...), principal: object = Depends(check_auth)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        job_id = payload.job_id
        ensure_job_access(job_id, principal, allow_missing=True)
        result = documents_agent_choose_reply(message, job_id=job_id, session_id=payload.session_id)
        return ok_response(result, **result)

    return router
