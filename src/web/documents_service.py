from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.web.documents_agent_chat import choose_documents_agent_reply
from src.web.documents_presenter import build_documents_ui_payload
from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, DOCUMENT_RENDERER_VERSION, document_mode_kinds, normalize_document_mode
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type

_deps: dict[str, Any] = {}


def configure_documents_service(**deps: Any) -> None:
    _deps.update(deps)


def _require(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"documents_service dependency is not configured: {name}")
    return value


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clamp_done(done: int, total: int) -> int:
    return max(0, min(done, total)) if total > 0 else 0


def _document_count_per_row(document_mode: str | None) -> int:
    return len(document_mode_kinds(document_mode))


def _pdf_count_per_row(document_mode: str | None) -> int:
    return 1 if "kp" in document_mode_kinds(document_mode) else 0


def _docx_count_per_row(document_mode: str | None) -> int:
    # Adaptive and HTML KP engines render their final artifact directly to PDF.
    # DOCX remains a required final artifact only for contracts.
    return 1 if "contract" in document_mode_kinds(document_mode) else 0


def _expected_output_counts(
    *,
    document_mode: str | None,
    total_rows: int,
    generator_state: dict,
) -> tuple[int, int]:
    docx_per_row = _docx_count_per_row(document_mode)
    pdfs_per_row = _pdf_count_per_row(document_mode)
    expected_documents = total_rows * docx_per_row if total_rows > 0 else (
        max(
            _safe_int(generator_state.get("staged_docx_count")),
            _safe_int(generator_state.get("generated_docx_count")),
        )
        if docx_per_row > 0
        else 0
    )
    expected_pdf_documents = total_rows * pdfs_per_row if total_rows > 0 else (
        max(
            _safe_int(generator_state.get("pdf_total")),
            _safe_int(generator_state.get("pdf_processed")),
            _safe_int(generator_state.get("staged_pdf_count")),
        )
        if pdfs_per_row > 0
        else 0
    )
    return expected_documents, expected_pdf_documents


def _is_output_ready(
    *,
    status: str,
    generator_done: bool,
    philologist_done: bool,
    expected_documents: int,
    expected_pdf_documents: int,
    output_docx_count: int,
    output_pdf_count: int,
    output_file_count: int,
) -> bool:
    if status != "completed" or not generator_done or not philologist_done:
        return False
    if expected_documents + expected_pdf_documents <= 0:
        return output_file_count > 0
    return output_docx_count >= expected_documents and output_pdf_count >= expected_pdf_documents

def is_successful_documents_generation_locked(
    *,
    status: str,
    generator_state: dict,
    document_mode: str | None,
) -> bool:
    generator_document_mode = normalize_document_mode(generator_state.get("document_mode") or DOCUMENT_MODE_BOTH)
    requested_document_mode = normalize_document_mode(document_mode or DOCUMENT_MODE_BOTH)
    return (
        status == "completed"
        and generator_document_mode == requested_document_mode
        and _safe_int(generator_state.get("error_rows")) == 0
        and _safe_int(generator_state.get("output_file_count")) > 0
    )


def _documents_progress_units(
    *,
    status: str,
    stage: str,
    generator: dict,
    philologist: dict,
    total_rows: int,
    processed_rows: int,
    total_documents: int,
    reviewed_documents: int,
    output_file_count: int,
    document_mode: str | None,
) -> dict[str, Any]:
    documents_per_row = _document_count_per_row(document_mode)
    pdfs_per_row = _pdf_count_per_row(document_mode)
    expected_documents = total_rows * documents_per_row if total_rows > 0 else max(
        total_documents,
        _safe_int(generator.get("staged_docx_count")),
        _safe_int(generator.get("generated_docx_count")),
        _safe_int(generator.get("pdf_total")),
    )
    expected_pdf_documents = total_rows * pdfs_per_row if total_rows > 0 else max(
        _safe_int(generator.get("pdf_total")),
        _safe_int(generator.get("pdf_processed")),
        _safe_int(generator.get("staged_pdf_count")),
    )

    generated_total = expected_documents
    generated_done = max(
        _safe_int(generator.get("staged_docx_count")),
        _safe_int(generator.get("generated_docx_count")),
        processed_rows * documents_per_row if total_rows > 0 else 0,
    )
    if str(generator.get("status") or "") == "completed":
        generated_done = generated_total

    review_total = max(total_documents, expected_documents)
    review_done = reviewed_documents
    if str(philologist.get("status") or "") == "completed" or status == "completed":
        review_done = review_total

    finalize_total = expected_pdf_documents
    finalize_done = 0
    if stage == "ready":
        finalize_done = max(_safe_int(generator.get("pdf_processed")), _safe_int(generator.get("staged_pdf_count")))
    if status == "completed" or output_file_count >= expected_documents + expected_pdf_documents:
        finalize_done = finalize_total

    parts = [
        {
            "id": "generate",
            "title": "Создание документов",
            "done": _clamp_done(generated_done, generated_total),
            "total": generated_total,
        },
        {
            "id": "review",
            "title": "Проверка текста",
            "done": _clamp_done(review_done, review_total),
            "total": review_total,
        },
        {
            "id": "finalize",
            "title": "Сборка результата",
            "done": _clamp_done(finalize_done, finalize_total),
            "total": finalize_total,
        },
    ]
    done_units = sum(part["done"] for part in parts)
    total_units = sum(part["total"] for part in parts)
    percent = round((done_units / total_units) * 100) if total_units > 0 else 0
    if status == "completed":
        percent = 100
    elif status == "idle":
        percent = 0
    else:
        percent = min(percent, 99)
    return {
        "done": done_units,
        "total": total_units,
        "percent": max(0, min(100, percent)),
        "parts": parts,
    }


def _documents_current_item_text(*, status: str, stage: str, generator: dict, philologist: dict) -> str:
    return ""


def _recover_completed_generator_after_worker_exit(
    *,
    job_id: str | None,
    generator_state: dict,
    philologist_state: dict,
    readiness: dict,
    pipeline_thread: Any,
) -> dict:
    if pipeline_thread is not None:
        return generator_state
    if str(generator_state.get("status") or "") != "running":
        return generator_state
    if str(generator_state.get("stage") or "") != "finalize_output":
        return generator_state
    if str(philologist_state.get("status") or "") != "completed":
        return generator_state

    document_mode = normalize_document_mode(generator_state.get("document_mode") or DOCUMENT_MODE_BOTH)
    docx_per_row = _docx_count_per_row(document_mode)
    pdfs_per_row = _pdf_count_per_row(document_mode)
    expected_documents = _safe_int(generator_state.get("total_rows")) * docx_per_row
    if expected_documents <= 0 and docx_per_row > 0:
        expected_documents = max(
            _safe_int(generator_state.get("staged_docx_count")),
            _safe_int(generator_state.get("generated_docx_count")),
        )
    expected_pdf_documents = _safe_int(generator_state.get("total_rows")) * pdfs_per_row
    output_docx_count = _safe_int(readiness.get("output_docx_count"))
    output_pdf_count = _safe_int(readiness.get("output_pdf_count"))
    if (
        expected_documents + expected_pdf_documents <= 0
        or output_docx_count < expected_documents
        or output_pdf_count < expected_pdf_documents
    ):
        return generator_state

    recovered_state = dict(generator_state)
    recovered_state["status"] = "completed"
    recovered_state["stage"] = "completed"
    recovered_state["stage_text"] = "Результат собран."
    recovered_state["completed_at"] = recovered_state.get("completed_at") or datetime.now().isoformat(timespec="seconds")
    recovered_state["stop_requested"] = False
    recovered_state["stop_requested_at"] = None
    recovered_state["pdf_total"] = max(_safe_int(recovered_state.get("pdf_total")), expected_pdf_documents)
    recovered_state["pdf_processed"] = max(_safe_int(recovered_state.get("pdf_processed")), expected_pdf_documents)
    recovered_state["staged_pdf_count"] = max(_safe_int(recovered_state.get("staged_pdf_count")), expected_pdf_documents)
    recovered_state["output_file_count"] = max(
        _safe_int(recovered_state.get("output_file_count")),
        output_docx_count + output_pdf_count,
    )
    recovered_state["summary_text"] = "Документы проверены, результат собран."
    _require("save_generator_state")(recovered_state, job_id)
    return recovered_state


def _stop_orphaned_documents_worker_state(
    *,
    job_id: str | None,
    agent_name: str,
    state: dict,
    worker_thread: Any,
    pipeline_thread: Any,
) -> dict:
    status = str(state.get("status") or "idle")
    if status not in {"running", "finalizing"}:
        return state

    if agent_name == "philologist":
        total_documents = int(state.get("total_documents") or 0)
        processed_documents = int(state.get("processed_documents") or 0)
        if total_documents > 0 and processed_documents >= total_documents:
            completed_state = dict(state)
            completed_state["status"] = "completed"
            completed_state["completed_at"] = (
                completed_state.get("completed_at")
                or datetime.now().isoformat(timespec="seconds")
            )
            completed_state["stop_requested"] = False
            completed_state["stop_requested_at"] = None
            completed_state["summary_text"] = (
                completed_state.get("summary_text")
                or "Проверка текста завершена."
            )
            _require("save_philologist_state")(completed_state, job_id)
            return completed_state

    if worker_thread is not None or pipeline_thread is not None:
        return state
    if agent_name == "generator" and str(state.get("stage") or "") == "finalize_output":
        return state

    recovered_state = dict(state)
    recovered_state["status"] = "stopped"
    recovered_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    recovered_state["stop_requested"] = False
    recovered_state["stop_requested_at"] = None
    recovered_state["summary_text"] = (
        "Работа была остановлена после перезапуска сервиса. "
        "Можно продолжить с сохраненного места."
    )
    if agent_name == "generator":
        _require("save_generator_state")(recovered_state, job_id)
    elif agent_name == "philologist":
        _require("save_philologist_state")(recovered_state, job_id)
    return recovered_state


def compact_documents_status(job_id: str | None, document_mode: str | None = None) -> dict:
    compact_generator_status = _require("compact_generator_status")
    get_generator_status = _require("get_generator_status")
    compact_philologist_status = _require("compact_philologist_status")
    get_philologist_status = _require("get_philologist_status")
    get_documents_thread = _require("get_documents_thread")
    get_generator_thread = _require("get_generator_thread")
    get_philologist_thread = _require("get_philologist_thread")

    generator_state = compact_generator_status(get_generator_status(job_id))
    philologist_state = compact_philologist_status(get_philologist_status(job_id, include_details=False))
    pipeline_thread = get_documents_thread(job_id)
    generator_state = _stop_orphaned_documents_worker_state(
        job_id=job_id,
        agent_name="generator",
        state=generator_state,
        worker_thread=get_generator_thread(job_id),
        pipeline_thread=pipeline_thread,
    )
    philologist_state = _stop_orphaned_documents_worker_state(
        job_id=job_id,
        agent_name="philologist",
        state=philologist_state,
        worker_thread=get_philologist_thread(job_id),
        pipeline_thread=pipeline_thread,
    )
    readiness = _require("build_job_readiness_result")(job_id, document_mode=document_mode)
    generator_state = _recover_completed_generator_after_worker_exit(
        job_id=job_id,
        generator_state=generator_state,
        philologist_state=philologist_state,
        readiness=readiness,
        pipeline_thread=pipeline_thread,
    )
    generator_status = str(generator_state.get("status") or "idle")
    philologist_status = str(philologist_state.get("status") or "idle")
    document_mode = normalize_document_mode(document_mode or generator_state.get("document_mode") or DOCUMENT_MODE_BOTH)
    work_type = normalize_work_type(generator_state.get("work_type") or DEFAULT_WORK_TYPE)
    generator_done = generator_status == "completed"
    reviewed_documents = int(philologist_state.get("processed_documents") or 0)
    total_documents = int(philologist_state.get("total_documents") or 0)
    philologist_done = philologist_status == "completed" or (
        total_documents > 0
        and reviewed_documents >= total_documents
        and philologist_status in {"running", "finalizing"}
    )

    if generator_status == "error" or philologist_status == "error":
        status = "error"
    elif generator_status == "stopped" or philologist_status == "stopped":
        status = "stopped"
    elif generator_done and philologist_done:
        status = "completed"
    elif pipeline_thread is not None or generator_status == "running" or philologist_status in {"running", "finalizing"}:
        status = "running"
    elif generator_done:
        status = "waiting_review"
    else:
        status = generator_status if generator_status in {"completed", "error", "stopped"} else "idle"

    generator_stage = str(generator_state.get("stage") or "")
    if not generator_done and philologist_status in {"running", "finalizing"}:
        stage = "review"
        stage_text = "Проверяем текст."
    elif not generator_done and generator_stage in {"convert_pdf", "finalize_output"}:
        stage = "ready"
        stage_text = "Собираем результат."
    elif not generator_done:
        stage = "generate"
        stage_text = "Готовим документы."
    elif not philologist_done:
        stage = "review"
        stage_text = "Проверяем текст."
    else:
        stage = "completed"
        stage_text = "Результат собран."
    progress_percent = 0

    if status == "idle":
        stage_text = "Подготовка документов ещё не запускалась."
    elif status == "waiting_review":
        stage_text = "Документы готовы. Можно запустить проверку текста."
    elif status == "stopped":
        stage_text = "Работа остановлена. Можно продолжить с сохраненного места."
    elif status == "error":
        stage_text = (
            generator_state.get("summary_text")
            or philologist_state.get("summary_text")
            or "Не удалось завершить подготовку документов."
        )

    documents_per_row = _document_count_per_row(document_mode)
    total_rows = int(generator_state.get("total_rows") or 0)
    if total_rows <= 0:
        total_rows = (
            int(philologist_state.get("total_documents") or 0)
            // max(1, documents_per_row)
        )
    processed_rows = int(generator_state.get("processed_rows") or 0)
    if generator_done and total_rows:
        processed_rows = total_rows
    expected_documents, expected_pdf_documents = _expected_output_counts(
        document_mode=document_mode,
        total_rows=total_rows,
        generator_state=generator_state,
    )
    output_file_count = int(generator_state.get("output_file_count") or 0)
    output_docx_count = _safe_int(readiness.get("output_docx_count"))
    output_pdf_count = _safe_int(readiness.get("output_pdf_count"))
    output_ready = _is_output_ready(
        status=status,
        generator_done=generator_done,
        philologist_done=philologist_done,
        expected_documents=expected_documents,
        expected_pdf_documents=expected_pdf_documents,
        output_docx_count=output_docx_count,
        output_pdf_count=output_pdf_count,
        output_file_count=output_file_count,
    )
    progress_units = _documents_progress_units(
        status=status,
        stage=stage,
        generator=generator_state,
        philologist=philologist_state,
        total_rows=total_rows,
        processed_rows=processed_rows,
        total_documents=total_documents,
        reviewed_documents=reviewed_documents,
        output_file_count=output_file_count,
        document_mode=document_mode,
    )
    progress_percent = progress_units["percent"]
    current_item_text = _documents_current_item_text(
        status=status,
        stage=stage,
        generator=generator_state,
        philologist=philologist_state,
    )
    restart_locked = output_ready and is_successful_documents_generation_locked(
        status=status,
        generator_state=generator_state,
        document_mode=document_mode,
    )
    result = {
        "job_id": job_id or "",
        "status": status,
        "stage": stage,
        "stage_text": stage_text,
        "current_item_text": current_item_text,
        "progress_percent": max(0, min(100, progress_percent)),
        "progress_units": progress_units,
        "generator": generator_state,
        "philologist": philologist_state,
        "total_rows": total_rows,
        "processed_rows": processed_rows,
        "total_documents": total_documents,
        "reviewed_documents": reviewed_documents,
        "error_rows": int(generator_state.get("error_rows") or 0),
        "fixed_documents": int(philologist_state.get("fixed_documents") or 0),
        "documents_with_issues": int(philologist_state.get("documents_with_issues") or 0),
        "output_file_count": output_file_count,
        "output_docx_count": output_docx_count,
        "output_pdf_count": output_pdf_count,
        "expected_output_docx_count": expected_documents,
        "expected_output_pdf_count": expected_pdf_documents,
        "output_ready": output_ready,
        "summary_text": stage_text,
        "document_mode": document_mode,
        "work_type": work_type,
        "restart_locked": restart_locked,
    }
    result["ui"] = build_documents_ui_payload(result, readiness=readiness)
    return result


def _documents_pipeline_stop_requested(job_id: str | None) -> bool:
    generator_state = _require("load_generator_state")(job_id)
    philologist_state = _require("load_philologist_state")(job_id)
    return bool(generator_state.get("stop_requested")) or bool(philologist_state.get("stop_requested"))


def _mark_documents_waiting_review_stopped(job_id: str | None) -> None:
    philologist_state = _require("load_philologist_state")(job_id)
    if str(philologist_state.get("status") or "") == "completed":
        return
    philologist_state["status"] = "stopped"
    philologist_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    philologist_state["summary_text"] = (
        "Подготовка остановлена после создания документов. "
        "Проверку текста можно запустить позже."
    )
    _require("save_philologist_state")(philologist_state, job_id)


def documents_agent_choose_reply(message: str, job_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    return choose_documents_agent_reply(
        message,
        job_id=job_id,
        status_loader=compact_documents_status,
        chat_with_orchestrator=_deps.get("chat_with_orchestrator"),
        session_id=session_id,
    )


def run_documents_pipeline_background(
    *,
    xlsx_path: Path,
    job_id: str | None,
    mode: str | None,
    document_mode: str | None = None,
    work_type: str | None = None,
) -> None:
    try:
        effective_document_mode = normalize_document_mode(document_mode or DOCUMENT_MODE_BOTH)
        effective_work_type = normalize_work_type(work_type or DEFAULT_WORK_TYPE)
        get_generator_status = _require("get_generator_status")
        clear_generator_stop_request = _require("clear_generator_stop_request")
        run_generator_agent = _require("run_generator_agent")
        get_philologist_status = _require("get_philologist_status")
        clear_philologist_stop_request = _require("clear_philologist_stop_request")
        run_philologist = _require("run_philologist")
        finalize_documents_output = _require("finalize_documents_output")
        schedule_output_archive_build = _require("schedule_output_archive_build")

        generator_state = get_generator_status(job_id)
        generator_document_mode = normalize_document_mode(generator_state.get("document_mode") or DOCUMENT_MODE_BOTH)
        generator_work_type = normalize_work_type(generator_state.get("work_type") or DEFAULT_WORK_TYPE)
        generator_renderer_version = str(generator_state.get("renderer_version") or "")
        generator_reran = False
        if (
            str(generator_state.get("status") or "") != "completed"
            or generator_document_mode != effective_document_mode
            or generator_work_type != effective_work_type
            or generator_renderer_version != DOCUMENT_RENDERER_VERSION
        ):
            clear_generator_stop_request(job_id)
            generator_state = run_generator_agent(
                xlsx_path=xlsx_path,
                job_id=job_id,
                create_pdf=False,
                auto_run_philologist=False,
                document_mode=effective_document_mode,
                work_type=effective_work_type,
            )
            generator_reran = True

        if str(generator_state.get("status") or "") != "completed":
            return

        if _documents_pipeline_stop_requested(job_id):
            _mark_documents_waiting_review_stopped(job_id)
            return

        philologist_state = get_philologist_status(job_id, include_details=False)
        philologist_document_mode = normalize_document_mode(philologist_state.get("document_mode") or DOCUMENT_MODE_BOTH)
        philologist_work_type = normalize_work_type(philologist_state.get("work_type") or DEFAULT_WORK_TYPE)
        if (
            generator_reran
            or str(philologist_state.get("status") or "") != "completed"
            or philologist_document_mode != effective_document_mode
            or philologist_work_type != effective_work_type
        ):
            if _documents_pipeline_stop_requested(job_id):
                _mark_documents_waiting_review_stopped(job_id)
                return
            clear_philologist_stop_request(job_id)
            philologist_state = run_philologist(ai_enabled=True, job_id=job_id, mode=mode or "fast")
            if isinstance(philologist_state, dict):
                philologist_state["document_mode"] = effective_document_mode
                philologist_state["work_type"] = effective_work_type
                _require("save_philologist_state")(philologist_state, job_id)

        if isinstance(philologist_state, dict) and philologist_state.get("status") == "completed":
            generator_state = finalize_documents_output(job_id=job_id)
            if not isinstance(generator_state, dict) or generator_state.get("status") != "completed":
                return
            schedule_output_archive_build(job_id)
    except Exception as exc:
        _require("logger").exception("documents_pipeline_failed", job_id=job_id)
        generator_state = _require("load_generator_state")(job_id)
        philologist_state = _require("load_philologist_state")(job_id)
        if str(generator_state.get("status") or "") == "running":
            generator_state["status"] = "error"
            generator_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
            generator_state["summary_text"] = f"Подготовка документов остановилась с ошибкой: {type(exc).__name__}: {exc}"
            _require("save_generator_state")(generator_state, job_id)
        elif str(philologist_state.get("status") or "") in {"running", "finalizing"}:
            philologist_state["status"] = "error"
            philologist_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
            philologist_state["summary_text"] = f"Проверка документов остановилась с ошибкой: {type(exc).__name__}: {exc}"
            _require("save_philologist_state")(philologist_state, job_id)
    finally:
        _require("unregister_documents_thread")(job_id)
