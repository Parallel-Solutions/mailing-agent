from __future__ import annotations

import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from src.generator.agent_handoff import (
    count_tasks_for_agent,
    create_task,
    get_recent_events,
    get_tasks_for_agent,
    mark_tasks_in_progress,
    set_task_statuses,
)
from src.generator.ai_case_agent import (
    CASE_AGENT_ONLY_SUSPICIOUS,
    ENABLE_CASE_AGENT,
    apply_case_agent_result,
    run_case_validation_agent,
)
from src.generator.config_generator import (
    BATCH_PDF_DIR,
    DATA_XLSX_PATH,
    DOCX_WORKERS,
    START_OUTGOING_NUMBER,
    WEB_CASE_AGENT_MAX_WORKERS,
)
from src.generator.document_builder import cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.excel_io import load_rows
from src.generator.pdf_converter import convert_docx_batch
from src.generator.responsibility_matrix import diagnose_responsibility
from src.generator.transforms import build_document_context
from src.jobs import load_agent_state, resolve_job_paths, save_agent_state
from src.utils.config import settings
from src.utils.logger import logger


GENERATOR_STATE: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "total_rows": 0,
    "processed_rows": 0,
    "ok_rows": 0,
    "error_rows": 0,
    "summary_text": "Агент-генератор ещё не запускался.",
    "results": [],
    "task_stats": {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0},
    "tasks": [],
    "recent_events": [],
}


def _load_generator_state(job_id: str | None = None) -> dict[str, Any]:
    return load_agent_state("generator", GENERATOR_STATE, job_id)


def _save_generator_state(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    return save_agent_state("generator", state, job_id)


def _format_generator_summary(
    state: dict[str, Any],
    *,
    review_handoffs: int = 0,
    philologist_started_rows: int = 0,
) -> str:
    base = (
        f"Агент-генератор завершил обработку: всего {state.get('total_rows', 0)}, "
        f"успешно {state.get('ok_rows', 0)}, ошибок {state.get('error_rows', 0)}."
    )
    if review_handoffs > 0:
        base += f" Подготовил для филолога {review_handoffs} задач на проверку."
    if philologist_started_rows > 0:
        base += f" Филолог автоматически запущен по {philologist_started_rows} строкам."
    return base


def cleanup_batch_pdf_dir(batch_pdf_dir: Path | None = None) -> None:
    target_dir = batch_pdf_dir or BATCH_PDF_DIR
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)


def process_generator_row(
    payload: tuple[int, int, dict],
    *,
    output_dir: Path | None = None,
    batch_docx_dir: Path | None = None,
    templates_dir: Path | None = None,
) -> dict:
    result_index, outgoing_number, row = payload
    context = build_document_context(row, outgoing_number)
    agent_result = run_case_validation_agent(row, context)
    context = apply_case_agent_result(context, agent_result)
    generated_files = generate_documents_for_row(
        row,
        context,
        output_dir=output_dir,
        batch_docx_dir=batch_docx_dir,
        templates_dir=templates_dir,
    )
    return {
        "result_index": result_index,
        "id": row.get("ID"),
        "status": "ok",
        "case_agent_status": context.get("CASE_AGENT_STATUS"),
        "generated_files": generated_files,
    }


def build_docx_jobs(results: list[dict]) -> list[dict[str, Path]]:
    jobs: list[dict[str, Path]] = []
    for result in results:
        generated_files = result.get("generated_files") or {}
        for job_key in ("kp", "contract"):
            staged_key = job_key
            final_docx_key = f"{job_key}_final_docx"
            final_pdf_key = f"{job_key}_final_pdf"
            if staged_key not in generated_files:
                continue
            jobs.append(
                {
                    "staged_docx": generated_files[staged_key],
                    "final_docx": generated_files[final_docx_key],
                    "final_pdf": generated_files[final_pdf_key],
                    "result_index": result["result_index"],
                    "file_kind": job_key,
                }
            )
    return jobs


def finalize_generated_files(results: list[dict], *, batch_pdf_dir: Path | None = None) -> None:
    jobs = build_docx_jobs(results)
    staged_docx_paths = [job["staged_docx"] for job in jobs]
    pdf_target_dir = batch_pdf_dir or BATCH_PDF_DIR
    pdf_map = convert_docx_batch(staged_docx_paths, pdf_target_dir, chunk_size=100, worker_count=1)

    for job in jobs:
        staged_docx = job["staged_docx"]
        final_docx = job["final_docx"]
        final_pdf = job["final_pdf"]

        final_docx.parent.mkdir(parents=True, exist_ok=True)
        if staged_docx.exists():
            shutil.copy2(str(staged_docx), str(final_docx))

        batch_pdf = pdf_map.get(staged_docx)
        pdf_created = bool(batch_pdf and batch_pdf.exists())
        if pdf_created:
            final_pdf.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(batch_pdf), str(final_pdf))

        result_entry = results[job["result_index"]]
        result_files = result_entry.setdefault("files", {})
        result_files[f"{job['file_kind']}_final_docx"] = str(final_docx)
        if pdf_created:
            result_files[f"{job['file_kind']}_final_pdf"] = str(final_pdf)

        if staged_docx.exists():
            staged_docx.unlink()

    for result in results:
        result.pop("generated_files", None)


def run_generator_agent(
    *,
    xlsx_path: Path | None = None,
    limit: int | None = None,
    row_ids: list[str] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    source_path = xlsx_path or (job_paths.data_xlsx if job_paths.data_xlsx.exists() else DATA_XLSX_PATH)
    claimed_tasks = mark_tasks_in_progress("generator", limit=limit, job_id=job_id)
    state = _load_generator_state(job_id)
    state.update(
        {
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "total_rows": 0,
            "processed_rows": 0,
            "ok_rows": 0,
            "error_rows": 0,
            "summary_text": (
                "Агент-генератор начал обработку строк."
                if not claimed_tasks
                else f"Агент-генератор начал обработку строк и принял {len(claimed_tasks)} внутренних задач."
            ),
            "results": [],
            "philologist_result": None,
            "task_stats": count_tasks_for_agent("generator", job_id),
            "tasks": get_tasks_for_agent("generator", job_id)[:20],
            "recent_events": get_recent_events(agent_name="generator", limit=20, job_id=job_id),
        }
    )
    _save_generator_state(state, job_id)

    if not source_path.exists():
        state["status"] = "error"
        state["summary_text"] = "Файл data.xlsx не найден."
        _save_generator_state(state, job_id)
        return dict(state)

    _, _, rows = load_rows(source_path)
    if not rows:
        state["status"] = "error"
        state["summary_text"] = "Нет данных для генерации."
        _save_generator_state(state, job_id)
        return dict(state)

    requested_row_ids = {str(item).strip() for item in (row_ids or []) if str(item).strip()}
    if requested_row_ids:
        rows = [row for row in rows if str(row.get("ID")).strip() in requested_row_ids]
    if limit:
        rows = rows[:limit]

    if not rows:
        state["status"] = "completed"
        state["summary_text"] = "Для генератора не нашлось строк под текущую задачу."
        state["task_stats"] = count_tasks_for_agent("generator", job_id)
        state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
        state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
        _save_generator_state(state, job_id)
        return dict(state)

    cleanup_batch_docx_dir(job_paths.batch_docx_dir if not job_paths.uses_legacy_layout else None)
    cleanup_batch_pdf_dir(job_paths.batch_pdf_dir if not job_paths.uses_legacy_layout else None)

    started_at = perf_counter()
    payloads = [(index, START_OUTGOING_NUMBER + index, row) for index, row in enumerate(rows)]
    row_lookup = {_safe_id(row.get("ID")): row for row in rows}
    results = [
        {
            "result_index": index,
            "id": row.get("ID"),
            "status": "error",
            "error": "Generation did not complete",
            "files": {},
        }
        for index, _, row in payloads
    ]

    logger.info("generator_agent_start", row_count=len(payloads))
    state["total_rows"] = len(payloads)
    _save_generator_state(state, job_id)

    if ENABLE_CASE_AGENT:
        for payload in payloads:
            result_index, _, row = payload
            try:
                results[result_index] = process_generator_row(
                    payload,
                    output_dir=None if job_paths.uses_legacy_layout else job_paths.output_dir,
                    batch_docx_dir=None if job_paths.uses_legacy_layout else job_paths.batch_docx_dir,
                    templates_dir=None if job_paths.uses_legacy_layout else job_paths.templates_dir,
                )
            except Exception as exc:
                results[result_index] = {
                    "result_index": result_index,
                    "id": row.get("ID"),
                    "status": "error",
                    "error": str(exc),
                    "files": {},
                }
            state["processed_rows"] += 1
            _save_generator_state(state, job_id)
    else:
        max_workers = max(1, min(DOCX_WORKERS, len(payloads)))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    process_generator_row,
                    payload,
                    output_dir=None if job_paths.uses_legacy_layout else job_paths.output_dir,
                    batch_docx_dir=None if job_paths.uses_legacy_layout else job_paths.batch_docx_dir,
                    templates_dir=None if job_paths.uses_legacy_layout else job_paths.templates_dir,
                ): payload
                for payload in payloads
            }
            for future in as_completed(future_map):
                result_index, _, row = future_map[future]
                try:
                    results[result_index] = future.result()
                except Exception as exc:
                    results[result_index] = {
                        "result_index": result_index,
                        "id": row.get("ID"),
                        "status": "error",
                        "error": str(exc),
                        "files": {},
                    }
                state["processed_rows"] += 1
                _save_generator_state(state, job_id)

    finalize_generated_files(
        results,
        batch_pdf_dir=None if job_paths.uses_legacy_layout else job_paths.batch_pdf_dir,
    )
    review_handoffs = 0
    review_row_ids: list[str] = []
    for result in results:
        result.pop("result_index", None)
        result_id = _safe_id(result.get("id"))
        row = row_lookup.get(result_id) or {}
        mun_name = str(row.get("MUN_NAME") or "")
        if result.get("status") == "ok":
            set_task_statuses(
                "generator",
                row_id=result.get("id"),
                new_status="done",
                note="Генератор пересобрал или подтвердил комплект документов.",
                resolution_summary="Комплект документов собран и сохранён в output.",
                job_id=job_id,
            )
            if settings.inter_agent_handoffs_enabled:
                diagnosis = diagnose_responsibility(
                    symptom="documents_ready_for_review",
                    context={"row_id": result.get("id")},
                )
                create_task(
                    source_agent="generator",
                    target_agent=diagnosis["owner_agent"],
                    owner_agent=diagnosis["owner_agent"],
                    task_type="review_generated_documents",
                    problem_type=diagnosis["problem_type"],
                    symptom="documents_ready_for_review",
                    root_cause=diagnosis["root_cause"],
                    priority=diagnosis["priority"],
                    blocking=diagnosis["blocking"],
                    can_retry_after=diagnosis["can_retry_after"],
                    row_id=result.get("id"),
                    mun_name=mun_name,
                    details={
                        "reason": "Документы готовы и требуют языковой проверки.",
                        "files": result.get("files") or {},
                    },
                    job_id=job_id,
                )
                review_handoffs += 1
            if result_id:
                review_row_ids.append(result_id)
        else:
            set_task_statuses(
                "generator",
                row_id=result.get("id"),
                new_status="blocked",
                note=str(result.get("error") or "Генератор не смог собрать документы."),
                resolution_summary="Комплект документов не собран.",
                job_id=job_id,
            )

    philologist_started_rows = 0
    philologist_result = None
    if review_row_ids and settings.philologist_auto_run_enabled:
        from src.generator.philologist_agent import run_philologist

        philologist_result = run_philologist(ai_enabled=True, row_ids=review_row_ids, job_id=job_id)
        philologist_started_rows = len(review_row_ids)

    state["results"] = results
    state["ok_rows"] = sum(1 for item in results if item.get("status") == "ok")
    state["error_rows"] = sum(1 for item in results if item.get("status") == "error")
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
    state["status"] = "completed"
    if isinstance(philologist_result, dict):
        state["philologist_result"] = {
            "status": philologist_result.get("status"),
            "summary_text": philologist_result.get("summary_text"),
        }
    state["task_stats"] = count_tasks_for_agent("generator", job_id)
    state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
    state["summary_text"] = _format_generator_summary(
        state,
        review_handoffs=review_handoffs,
        philologist_started_rows=philologist_started_rows,
    )
    _save_generator_state(state, job_id)
    logger.info(
        "generator_agent_done",
        total=len(results),
        ok_count=state["ok_rows"],
        error_count=state["error_rows"],
        elapsed_seconds=state["elapsed_seconds"],
        case_agent_enabled=ENABLE_CASE_AGENT,
        case_agent_only_suspicious=CASE_AGENT_ONLY_SUSPICIOUS,
        web_case_agent_max_workers=WEB_CASE_AGENT_MAX_WORKERS,
    )
    return dict(state)


def get_generator_status(job_id: str | None = None) -> dict[str, Any]:
    state = _load_generator_state(job_id)
    state["task_stats"] = count_tasks_for_agent("generator", job_id)
    state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
    return state


def _safe_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
