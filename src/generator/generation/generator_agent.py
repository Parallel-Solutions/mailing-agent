from __future__ import annotations

import shutil
import json
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from src.generator.orchestration.agent_handoff import (
    count_tasks_for_agent,
    create_task,
    get_recent_events,
    get_tasks_for_agent,
    mark_tasks_in_progress,
    set_task_statuses,
)
from src.generator.inflection.ai_case_agent import (
    CASE_AGENT_ONLY_SUSPICIOUS,
    ENABLE_CASE_AGENT,
    apply_case_agent_result,
    run_case_validation_agent,
)
from src.generator.generation.config_generator import (
    BATCH_PDF_DIR,
    DATA_XLSX_PATH,
    DOCX_WORKERS,
    OUTPUT_DIR,
    PDF_CHUNK_SIZE,
    PDF_WORKERS,
    START_OUTGOING_NUMBER,
    WEB_CASE_AGENT_MAX_WORKERS,
)
from src.generator.generation.document_builder import (
    CONTRACT_TEMPLATE_FILENAME,
    DOCUMENT_MODE_BOTH,
    DOCUMENT_RENDERER_VERSION,
    KP_TEMPLATE_FILENAME,
    cleanup_batch_docx_dir,
    document_mode_kinds,
    generate_documents_for_row,
    normalize_document_mode,
)
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type
from src.generator.generation.excel_io import load_rows
from src.generator.generation.pdf_converter import convert_docx_batch
from src.generator.orchestration.responsibility_matrix import diagnose_responsibility
from src.generator.generation.transforms import build_document_context
from src.generator.philologist.document_review_agent import review_docx
from src.generator.philologist.philologist_agent import _auto_fix_docx
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
    "stage": "idle",
    "stage_text": "Подготовка документов ещё не запускалась.",
    "staged_docx_count": 0,
    "staged_pdf_count": 0,
    "pdf_total": 0,
    "pdf_processed": 0,
    "output_file_count": 0,
    "summary_text": "Подготовка документов ещё не запускалась.",
    "results": [],
    "current_client": None,
    "template_review": {},
    "completed_result_indices": [],
    "stop_requested": False,
    "stop_requested_at": None,
    "task_stats": {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0},
    "tasks": [],
    "recent_events": [],
    "timings": [],
    "document_mode": DOCUMENT_MODE_BOTH,
    "work_type": DEFAULT_WORK_TYPE,
    "renderer_version": DOCUMENT_RENDERER_VERSION,
}
_STATUS_FILE_COUNT_CACHE_LOCK = threading.Lock()
_STATUS_FILE_COUNT_CACHE: dict[str, dict[str, float | int]] = {}
STATUS_FILE_COUNT_CACHE_TTL_SECONDS = 10.0


class GeneratorStopRequested(RuntimeError):
    """Soft stop signal for background generator execution."""


def _load_generator_state(job_id: str | None = None, *, include_details: bool = True) -> dict[str, Any]:
    return load_agent_state("generator", GENERATOR_STATE, job_id, include_details=include_details)


def _save_generator_state(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    return save_agent_state("generator", state, job_id)


def _timing_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_timing_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _timing_safe(item) for key, item in value.items()}
    return str(value)


def _record_generator_timing(
    state: dict[str, Any],
    job_id: str | None,
    stage: str,
    started: float,
    **details: Any,
) -> dict[str, Any]:
    elapsed = round(perf_counter() - started, 3)
    item = {
        "stage": stage,
        "seconds": elapsed,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        **{key: _timing_safe(value) for key, value in details.items()},
    }
    timings = list(state.get("timings") or [])
    timings.append(item)
    state["timings"] = timings[-100:]
    logger.info("generator_stage_timing", job_id=job_id, **item)
    _save_generator_state(state, job_id)
    return item


def _cached_file_count(directory: Path, pattern: str, *, recursive: bool = False) -> int:
    if not directory.exists():
        return 0
    cache_key = f"{directory.resolve()}::{pattern}::{int(recursive)}"
    now = time.monotonic()
    with _STATUS_FILE_COUNT_CACHE_LOCK:
        cached = _STATUS_FILE_COUNT_CACHE.get(cache_key)
        if cached and now - float(cached.get("cached_at") or 0.0) <= STATUS_FILE_COUNT_CACHE_TTL_SECONDS:
            return int(cached.get("count") or 0)
    iterator = directory.rglob(pattern) if recursive else directory.glob(pattern)
    count = sum(1 for path in iterator if path.is_file())
    with _STATUS_FILE_COUNT_CACHE_LOCK:
        _STATUS_FILE_COUNT_CACHE[cache_key] = {"cached_at": now, "count": count}
    return count


def request_generator_stop(job_id: str | None = None) -> dict[str, Any]:
    state = _load_generator_state(job_id)
    state["stop_requested"] = True
    state["stop_requested_at"] = datetime.now().isoformat(timespec="seconds")
    if state.get("status") == "running":
        state["summary_text"] = (
            "Остановку приняла. Завершу текущий безопасный шаг и остановлю подготовку документов."
        )
    _save_generator_state(state, job_id)
    return get_generator_status(job_id)


def clear_generator_stop_request(job_id: str | None = None) -> None:
    state = _load_generator_state(job_id)
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _save_generator_state(state, job_id)


def _refresh_generator_stop_flag(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    persisted = _load_generator_state(job_id)
    state["stop_requested"] = bool(persisted.get("stop_requested", False))
    state["stop_requested_at"] = persisted.get("stop_requested_at")
    return state


def _result_from_state(result: dict[str, Any]) -> dict[str, Any]:
    restored = dict(result)
    for container_key in ("generated_files", "files"):
        container = restored.get(container_key)
        if not isinstance(container, dict):
            continue
        restored[container_key] = {
            key: (Path(value) if isinstance(value, str) and value else value)
            for key, value in container.items()
        }
    return restored


def _results_from_state(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [_result_from_state(item) for item in (items or []) if isinstance(item, dict)]


def _format_generator_summary(
    state: dict[str, Any],
    *,
    review_handoffs: int = 0,
    philologist_started_rows: int = 0,
) -> str:
    total_rows = int(state.get("total_rows", 0) or 0)
    ok_rows = int(state.get("ok_rows", 0) or 0)
    error_rows = int(state.get("error_rows", 0) or 0)

    if error_rows > 0:
        base = (
            "Документы подготовлены не полностью. "
            "Часть строк требует внимания, подробности можно посмотреть в журнале."
        )
    elif ok_rows > 0 or total_rows > 0:
        if philologist_started_rows > 0:
            base = "Документы подготовлены, проверка текста запущена."
        elif review_handoffs > 0:
            base = "Документы подготовлены. Тексты переданы на проверку."
        else:
            base = "Документы подготовлены."
    else:
        base = "Подготовка документов завершена."

    template_review = state.get("template_review") or {}
    template_applied = int(template_review.get("applied_fix_count", 0) or 0)
    template_documents = int(template_review.get("checked_templates", 0) or 0)
    if template_documents > 0 and template_applied > 0:
        base += " Шаблоны были аккуратно исправлены перед подготовкой документов."
    inflection_summary = state.get("inflection_summary") or {}
    if inflection_summary.get("total"):
        warning_count = int(inflection_summary.get("warning_count", 0) or 0)
        if warning_count > 0:
            base += " В некоторых местах лучше проверить текст вручную."
    if review_handoffs > 0:
        base += " Тексты переданы на дополнительную проверку."
    if philologist_started_rows > 0:
        base += " Проверка текстов запущена."
    return base


def _template_review_report_items(template_review: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for item in template_review.get("templates", []) or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "Шаблон")
        issue_count = int(item.get("issue_count", 0) or 0)
        applied = int(item.get("applied_fix_count", 0) or 0)
        skipped = int(item.get("skipped_fix_count", 0) or 0)
        items.append(f"{label}: найдено {issue_count}, исправлено {applied}, оставлено на ручную проверку {skipped}")
    return items


def review_templates_before_generation(job_id: str | None, *, document_mode: str | None = None) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    templates_dir = None if job_paths.uses_legacy_layout else job_paths.templates_dir
    if templates_dir is None:
        return {"checked_templates": 0, "applied_fix_count": 0, "skipped_fix_count": 0, "issue_count": 0, "templates": []}

    requested_kinds = set(document_mode_kinds(document_mode))
    templates = []
    if "kp" in requested_kinds:
        templates.append(("КП", templates_dir / KP_TEMPLATE_FILENAME))
    if "contract" in requested_kinds:
        templates.append(("Договор", templates_dir / CONTRACT_TEMPLATE_FILENAME))
    report: dict[str, Any] = {
        "checked_templates": 0,
        "issue_count": 0,
        "applied_fix_count": 0,
        "skipped_fix_count": 0,
        "templates": [],
    }
    for label, path in templates:
        if not path.exists():
            continue
        review_result = review_docx(path, ai_enabled=False, force_full_review=True)
        fix_result = _auto_fix_docx(
            path,
            review_result,
            client=None,
            tool_runner=None,
            use_llm_strategy=False,
            use_rag_decisions=False,
        )
        report["checked_templates"] += 1
        report["issue_count"] += int(review_result.get("issue_count", 0) or 0)
        report["applied_fix_count"] += int(fix_result.get("applied_fix_count", 0) or 0)
        report["skipped_fix_count"] += int(fix_result.get("skipped_fix_count", 0) or 0)
        report["templates"].append(
            {
                "label": label,
                "path": str(path),
                "issue_count": int(review_result.get("issue_count", 0) or 0),
                "applied_fix_count": int(fix_result.get("applied_fix_count", 0) or 0),
                "skipped_fix_count": int(fix_result.get("skipped_fix_count", 0) or 0),
                "applied_fixes": fix_result.get("applied_fixes", []),
                "skipped_fixes": fix_result.get("skipped_fixes", []),
            }
        )
    report["summary_lines"] = _template_review_report_items(report)
    return report


def cleanup_batch_pdf_dir(batch_pdf_dir: Path | None = None) -> None:
    target_dir = batch_pdf_dir or BATCH_PDF_DIR
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)


def cleanup_existing_output_dirs(rows: list[dict], output_dir: Path | None) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    row_ids = {str(row.get("ID") or "").strip() for row in rows}
    row_ids.discard("")
    if not row_ids:
        return
    for path in output_dir.iterdir():
        if not path.is_dir():
            continue
        prefix = path.name.split("_", 1)[0].strip()
        if prefix in row_ids:
            shutil.rmtree(path, ignore_errors=True)


def process_generator_row(
    payload: tuple[int, int, dict],
    *,
    output_dir: Path | None = None,
    batch_docx_dir: Path | None = None,
    templates_dir: Path | None = None,
    document_mode: str | None = None,
    work_type: str | None = None,
) -> dict:
    result_index, outgoing_number, row = payload
    context = build_document_context(row, outgoing_number, work_type=work_type)
    agent_result = run_case_validation_agent(row, context)
    context = apply_case_agent_result(context, agent_result)
    generated_files = generate_documents_for_row(
        row,
        context,
        output_dir=output_dir,
        batch_docx_dir=batch_docx_dir,
        templates_dir=templates_dir,
        document_mode=document_mode,
    )
    return {
        "result_index": result_index,
        "id": row.get("ID"),
        "status": "ok",
        "case_agent_status": context.get("CASE_AGENT_STATUS"),
        "inflection_trace": context.get("INFLECTION_TRACE", []),
        "generated_files": generated_files,
    }


def build_inflection_summary(results: list[dict]) -> dict[str, Any]:
    by_method: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    warning_samples: list[dict[str, Any]] = []
    total = 0

    for result in results:
        row_id = result.get("id")
        for item in result.get("inflection_trace") or []:
            total += 1
            method = str(item.get("method") or "unknown")
            confidence = str(item.get("confidence") or "unknown")
            by_method[method] = by_method.get(method, 0) + 1
            by_confidence[confidence] = by_confidence.get(confidence, 0) + 1
            warning = str(item.get("warning") or "").strip()
            if warning and len(warning_samples) < 10:
                warning_samples.append(
                    {
                        "row_id": row_id,
                        "field": item.get("field"),
                        "source_value": item.get("source_value"),
                        "result_value": item.get("result_value"),
                        "warning": warning,
                    }
                )

    return {
        "total": total,
        "by_method": by_method,
        "by_confidence": by_confidence,
        "warning_count": sum(
            1
            for result in results
            for item in (result.get("inflection_trace") or [])
            if str(item.get("warning") or "").strip()
        ),
        "sample_warnings": warning_samples,
    }


def save_inflection_log(results: list[dict], *, log_path: Path | None) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        for result in results:
            for item in result.get("inflection_trace") or []:
                payload = {
                    "row_id": result.get("id"),
                    **item,
                }
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


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


def _finalize_generated_jobs(
    jobs: list[dict[str, Path]],
    results: list[dict],
    *,
    pdf_map: dict[Path, Path | None] | None = None,
    should_stop: Any | None = None,
    create_pdf: bool = False,
) -> None:
    pdf_map = pdf_map or {}
    for job in jobs:
        if should_stop and should_stop():
            raise GeneratorStopRequested("Остановка запрошена во время сборки output.")
        staged_docx = job["staged_docx"]
        final_docx = job["final_docx"]
        final_pdf = job["final_pdf"]

        final_docx.parent.mkdir(parents=True, exist_ok=True)
        staged_is_final_docx = False
        if staged_docx.exists():
            try:
                staged_is_final_docx = staged_docx.resolve() == final_docx.resolve()
            except OSError:
                staged_is_final_docx = False
            if not staged_is_final_docx:
                shutil.copy2(str(staged_docx), str(final_docx))

        batch_pdf = pdf_map.get(staged_docx) if create_pdf else None
        pdf_created = bool(batch_pdf and batch_pdf.exists())
        if pdf_created:
            final_pdf.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(batch_pdf), str(final_pdf))

        result_entry = results[job["result_index"]]
        result_files = result_entry.setdefault("files", {})
        if final_docx.exists():
            result_files[f"{job['file_kind']}_final_docx"] = str(final_docx)
        if final_pdf.exists():
            result_files[f"{job['file_kind']}_final_pdf"] = str(final_pdf)
        elif create_pdf and final_docx.exists():
            result_entry["status"] = "error"
            missing_pdf = f"{job['file_kind']}.pdf"
            existing_error = str(result_entry.get("error") or "").strip()
            pdf_error = f"Не удалось создать PDF: {missing_pdf}."
            result_entry["error"] = f"{existing_error} {pdf_error}".strip() if existing_error else pdf_error

        if staged_docx.exists() and not staged_is_final_docx:
            staged_docx.unlink()


def _validate_generated_result(result: dict[str, Any]) -> None:
    result_files = result.get("files") or {}
    if not result_files:
        result["status"] = "error"
        result["error"] = "Генератор не подготовил ожидаемые файлы."
        return

    missing_outputs: list[str] = []
    for kind in ("kp", "contract"):
        final_docx = result_files.get(f"{kind}_final_docx")
        if final_docx is None:
            continue
        final_docx_path = Path(final_docx) if final_docx else None
        if final_docx_path is None or not final_docx_path.exists():
            missing_outputs.append(f"{kind}.docx")

    if missing_outputs:
        result["status"] = "error"
        result["error"] = "Не созданы итоговые файлы: " + ", ".join(missing_outputs)


def finalize_generated_files(
    results: list[dict],
    *,
    batch_pdf_dir: Path | None = None,
    progress_callback: Any | None = None,
    should_stop: Any | None = None,
    chunk_size: int | None = None,
    worker_count: int | None = None,
    create_pdf: bool = False,
) -> None:
    chunk_size = max(1, int(chunk_size or PDF_CHUNK_SIZE))
    worker_count = max(1, int(worker_count or PDF_WORKERS))
    jobs = build_docx_jobs(results)
    pending_pdf_jobs = []
    ready_jobs = []
    for job in jobs:
        staged_docx = job["staged_docx"]
        final_docx = job["final_docx"]
        final_pdf = job["final_pdf"]
        final_is_ready = final_docx.exists() and (not create_pdf or _is_pdf_current(final_pdf, final_docx))
        if not staged_docx.exists() and final_is_ready:
            result_entry = results[job["result_index"]]
            result_files = result_entry.setdefault("files", {})
            result_files[f"{job['file_kind']}_final_docx"] = str(final_docx)
            if final_pdf.exists():
                result_files[f"{job['file_kind']}_final_pdf"] = str(final_pdf)
            continue
        if staged_docx.exists():
            pending_pdf_jobs.append(job)
        elif create_pdf and final_docx.exists() and not _is_pdf_current(final_pdf, final_docx):
            recovery_job = dict(job)
            recovery_job["staged_docx"] = final_docx
            pending_pdf_jobs.append(recovery_job)
        elif final_docx.exists():
            ready_jobs.append(job)

    if ready_jobs:
        _finalize_generated_jobs(
            ready_jobs,
            results,
            should_stop=should_stop,
            create_pdf=False,
        )

    if create_pdf:
        pdf_target_dir = batch_pdf_dir or BATCH_PDF_DIR
        batch_size = max(1, worker_count * max(1, chunk_size))
        for start in range(0, len(pending_pdf_jobs), batch_size):
            job_batch = pending_pdf_jobs[start : start + batch_size]
            staged_docx_paths = [job["staged_docx"] for job in job_batch if job["staged_docx"].exists()]
            pdf_map = convert_docx_batch(
                staged_docx_paths,
                pdf_target_dir,
                chunk_size=chunk_size,
                worker_count=worker_count,
                progress_callback=progress_callback,
            )
            _finalize_generated_jobs(
                job_batch,
                results,
                pdf_map=pdf_map,
                should_stop=should_stop,
                create_pdf=True,
            )
    elif pending_pdf_jobs:
        _finalize_generated_jobs(
            pending_pdf_jobs,
            results,
            should_stop=should_stop,
            create_pdf=False,
        )

    for result in results:
        result.pop("generated_files", None)


def _is_valid_pdf(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        if path.stat().st_size <= 4:
            return False
        with path.open("rb") as handle:
            return handle.read(4) == b"%PDF"
    except OSError:
        return False


def _is_pdf_current(pdf_path: Path, docx_path: Path) -> bool:
    if not _is_valid_pdf(pdf_path):
        return False
    try:
        return pdf_path.stat().st_mtime >= docx_path.stat().st_mtime
    except OSError:
        return False


def _count_output_files_now(output_dir: Path) -> int:
    return sum(1 for path in output_dir.rglob("*") if path.is_file()) if output_dir.exists() else 0


def finalize_output_pdfs_for_job(job_id: str | None = None) -> dict[str, Any]:
    finalize_started = perf_counter()
    job_paths = resolve_job_paths(job_id)
    output_dir = job_paths.output_dir if not job_paths.uses_legacy_layout else OUTPUT_DIR
    batch_pdf_dir = job_paths.batch_pdf_dir if not job_paths.uses_legacy_layout else BATCH_PDF_DIR
    staging_dir = batch_pdf_dir / "_final_docx_for_pdf"
    pdf_work_dir = batch_pdf_dir / "_final_pdf_output"
    state = _load_generator_state(job_id)

    docx_paths = sorted(path for path in output_dir.rglob("*.docx") if path.is_file()) if output_dir.exists() else []
    total = len(docx_paths)
    ready = [path for path in docx_paths if _is_pdf_current(path.with_suffix(".pdf"), path)]
    ready_set = set(ready)
    pending = [path for path in docx_paths if path not in ready_set]

    state.update(
        {
            "status": "running",
            "completed_at": None,
            "stage": "finalize_output",
            "stage_text": "Собираю результат.",
            "pdf_total": total,
            "pdf_processed": len(ready),
            "staged_pdf_count": len(ready),
            "output_file_count": _count_output_files_now(output_dir),
            "summary_text": "Проверка текста завершена. Собираю итоговый результат.",
        }
    )
    _save_generator_state(state, job_id)

    if not pending:
        state["status"] = "completed"
        state["stage"] = "completed"
        state["stage_text"] = "Результат собран."
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["pdf_processed"] = total
        state["staged_pdf_count"] = total
        state["output_file_count"] = _count_output_files_now(output_dir)
        state["summary_text"] = "Документы проверены, результат собран."
        _record_generator_timing(
            state,
            job_id,
            "finalize_output",
            finalize_started,
            total_documents=total,
            pending_documents=0,
            already_ready=len(ready),
        )
        _save_generator_state(state, job_id)
        return dict(state)

    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    if pdf_work_dir.exists():
        shutil.rmtree(pdf_work_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    pdf_work_dir.mkdir(parents=True, exist_ok=True)

    staged_to_final: dict[Path, Path] = {}
    for index, source_docx in enumerate(pending, start=1):
        staged_docx = staging_dir / f"{index:06d}_{source_docx.stem}.docx"
        shutil.copy2(str(source_docx), str(staged_docx))
        staged_to_final[staged_docx] = source_docx.with_suffix(".pdf")

    progress = {"processed": len(ready)}

    def _report_pdf_progress() -> None:
        progress["processed"] += 1
        state["pdf_processed"] = min(progress["processed"], total)
        state["staged_pdf_count"] = state["pdf_processed"]
        _save_generator_state(state, job_id)

    failed: list[str] = []
    try:
        pdf_map = convert_docx_batch(
            list(staged_to_final.keys()),
            pdf_work_dir,
            chunk_size=PDF_CHUNK_SIZE,
            worker_count=PDF_WORKERS,
            progress_callback=_report_pdf_progress,
        )
        for staged_docx, final_pdf in staged_to_final.items():
            created_pdf = pdf_map.get(staged_docx)
            if created_pdf and created_pdf.exists():
                final_pdf.parent.mkdir(parents=True, exist_ok=True)
                if final_pdf.exists():
                    final_pdf.unlink()
                shutil.move(str(created_pdf), str(final_pdf))
            if not _is_valid_pdf(final_pdf):
                failed.append(str(final_pdf))
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(pdf_work_dir, ignore_errors=True)

    state["pdf_processed"] = total - len(failed)
    state["staged_pdf_count"] = state["pdf_processed"]
    state["output_file_count"] = _count_output_files_now(output_dir)
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    _record_generator_timing(
        state,
        job_id,
        "finalize_output",
        finalize_started,
        total_documents=total,
        pending_documents=len(pending),
        failed_count=len(failed),
        workers=PDF_WORKERS,
        chunk_size=PDF_CHUNK_SIZE,
    )
    if failed:
        state["status"] = "error"
        state["stage"] = "finalize_output"
        state["summary_text"] = f"Не удалось собрать PDF для {len(failed)} файлов."
    else:
        state["status"] = "completed"
        state["stage"] = "completed"
        state["stage_text"] = "Результат собран."
        state["summary_text"] = "Документы проверены, результат собран."
    _save_generator_state(state, job_id)
    return dict(state)


def prime_generator_state(
    *,
    xlsx_path: Path | None = None,
    limit: int | None = None,
    row_ids: list[str] | None = None,
    job_id: str | None = None,
    document_mode: str | None = None,
    work_type: str | None = None,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    source_path = xlsx_path or (job_paths.data_xlsx if job_paths.data_xlsx.exists() else DATA_XLSX_PATH)
    state = _load_generator_state(job_id)

    if not source_path.exists():
        state["status"] = "error"
        state["summary_text"] = "Файл data.xlsx не найден."
        _save_generator_state(state, job_id)
        return dict(state)

    load_started = perf_counter()
    workbook, _, rows = load_rows(source_path)
    close = getattr(workbook, "close", None)
    if callable(close):
        close()
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
    effective_document_mode = normalize_document_mode(document_mode or state.get("document_mode") or DOCUMENT_MODE_BOTH)
    effective_work_type = normalize_work_type(work_type or state.get("work_type") or DEFAULT_WORK_TYPE)

    if not rows:
        state["status"] = "completed"
        state["summary_text"] = "В таблице не нашлось клиентов для подготовки документов."
        state["task_stats"] = count_tasks_for_agent("generator", job_id)
        state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
        state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
        _save_generator_state(state, job_id)
        return dict(state)

    state.update(
        {
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "total_rows": len(rows),
            "processed_rows": 0,
            "ok_rows": 0,
            "error_rows": 0,
            "stage": "review_templates",
            "stage_text": "Проверяю шаблоны перед подготовкой документов.",
            "document_mode": effective_document_mode,
            "work_type": effective_work_type,
            "renderer_version": DOCUMENT_RENDERER_VERSION,
            "staged_docx_count": 0,
            "staged_pdf_count": 0,
            "pdf_total": 0,
            "pdf_processed": 0,
            "output_file_count": 0,
            "summary_text": f"Начинаю подготовку документов для {len(rows)} клиентов. Сначала проверю шаблоны.",
            "results": [],
            "template_review": {},
            "inflection_summary": {},
            "municipality_name_verification": {},
            "philologist_result": None,
            "task_stats": count_tasks_for_agent("generator", job_id),
            "tasks": get_tasks_for_agent("generator", job_id)[:20],
            "recent_events": get_recent_events(agent_name="generator", limit=20, job_id=job_id),
            "current_client": None,
        }
    )
    _save_generator_state(state, job_id)
    _record_generator_timing(state, job_id, "load_rows", load_started, row_count=len(rows), source_path=source_path)
    return dict(state)


def run_generator_agent(
    *,
    xlsx_path: Path | None = None,
    limit: int | None = None,
    row_ids: list[str] | None = None,
    job_id: str | None = None,
    create_pdf: bool = True,
    auto_run_philologist: bool | None = None,
    document_mode: str | None = None,
    work_type: str | None = None,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    source_path = xlsx_path or (job_paths.data_xlsx if job_paths.data_xlsx.exists() else DATA_XLSX_PATH)
    claimed_tasks = mark_tasks_in_progress("generator", limit=limit, job_id=job_id)
    state = _load_generator_state(job_id)
    was_stopped = str(state.get("status") or "") == "stopped"

    if not source_path.exists():
        state["status"] = "error"
        state["summary_text"] = "Файл data.xlsx не найден."
        _save_generator_state(state, job_id)
        return dict(state)

    load_started = perf_counter()
    workbook, _, rows = load_rows(source_path)
    close = getattr(workbook, "close", None)
    if callable(close):
        close()
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
    effective_document_mode = normalize_document_mode(document_mode or state.get("document_mode") or DOCUMENT_MODE_BOTH)
    effective_work_type = normalize_work_type(work_type or state.get("work_type") or DEFAULT_WORK_TYPE)

    if not rows:
        state["status"] = "completed"
        state["summary_text"] = "В таблице не нашлось клиентов для подготовки документов."
        state["task_stats"] = count_tasks_for_agent("generator", job_id)
        state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
        state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
        _save_generator_state(state, job_id)
        return dict(state)

    restored_results = _results_from_state(state.get("results"))
    completed_result_indices = {
        int(item)
        for item in (state.get("completed_result_indices") or [])
        if str(item).strip().isdigit()
    }
    resume_stage = str(state.get("stage") or "render_docx")
    if not was_stopped:
        restored_results = []
        completed_result_indices = set()
        resume_stage = "render_docx"

    state.update(
        {
            "status": "running",
            "started_at": state.get("started_at") if was_stopped and state.get("started_at") else datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "total_rows": len(rows),
            "processed_rows": len(completed_result_indices) if was_stopped else 0,
            "ok_rows": state.get("ok_rows", 0) if was_stopped else 0,
            "error_rows": state.get("error_rows", 0) if was_stopped else 0,
            "stage": resume_stage,
            "document_mode": effective_document_mode,
            "work_type": effective_work_type,
            "renderer_version": DOCUMENT_RENDERER_VERSION,
            "stage_text": (
                "Проверяю шаблоны перед подготовкой документов."
                if resume_stage == "review_templates"
                else "Создаю документы по шаблонам."
                if resume_stage == "render_docx"
                else str(state.get("stage_text") or "")
            ),
            "summary_text": (
                f"Продолжаю подготовку документов с сохраненного места. Уже готово {len(completed_result_indices)} из {len(rows)} клиентов."
                if was_stopped
                else (
                    f"Начинаю подготовку документов для {len(rows)} клиентов. Сначала проверю шаблоны."
                    if not claimed_tasks
                    else f"Начинаю подготовку документов для {len(rows)} клиентов."
                )
            ),
            "results": restored_results,
            "template_review": state.get("template_review", {}) if was_stopped else {},
            "completed_result_indices": sorted(completed_result_indices),
            "stop_requested": False,
            "stop_requested_at": None,
            "task_stats": count_tasks_for_agent("generator", job_id),
            "tasks": get_tasks_for_agent("generator", job_id)[:20],
            "recent_events": get_recent_events(agent_name="generator", limit=20, job_id=job_id),
            "timings": state.get("timings", []) if was_stopped else [],
        }
    )
    _save_generator_state(state, job_id)
    _record_generator_timing(state, job_id, "load_rows", load_started, row_count=len(rows), source_path=source_path)

    if not was_stopped:
        cleanup_batch_docx_dir(job_paths.batch_docx_dir if not job_paths.uses_legacy_layout else None)
        cleanup_batch_pdf_dir(job_paths.batch_pdf_dir if not job_paths.uses_legacy_layout else None)
        cleanup_existing_output_dirs(rows, None if job_paths.uses_legacy_layout else job_paths.output_dir)

    started_at = perf_counter()
    payloads = [(index, START_OUTGOING_NUMBER + index, row) for index, row in enumerate(rows)]
    row_lookup = {_safe_id(row.get("ID")): row for row in rows}
    if restored_results:
        results = restored_results
    else:
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

    logger.info("generator_agent_start", row_count=len(payloads), document_mode=effective_document_mode, work_type=effective_work_type)
    state["total_rows"] = len(payloads)
    _save_generator_state(state, job_id)

    try:
        if resume_stage == "review_templates":
            _refresh_generator_stop_flag(state, job_id)
            if state.get("stop_requested"):
                raise GeneratorStopRequested("Генерация остановлена до начала обработки строк.")
            stage_started = perf_counter()
            template_review = review_templates_before_generation(job_id, document_mode=effective_document_mode)
            _record_generator_timing(
                state,
                job_id,
                "review_templates",
                stage_started,
                checked_templates=template_review.get("checked_templates", 0),
                applied_fix_count=template_review.get("applied_fix_count", 0),
                issue_count=template_review.get("issue_count", 0),
            )
            state["template_review"] = template_review
            state["stage"] = "render_docx"
            state["stage_text"] = "Создаю документы по шаблонам."
            if int(template_review.get("applied_fix_count", 0) or 0) > 0:
                state["summary_text"] = (
                    "Шаблоны проверены и безопасные правки применены. "
                    + " ".join(template_review.get("summary_lines", [])[:2])
                ).strip()
            elif int(template_review.get("checked_templates", 0) or 0) > 0:
                state["summary_text"] = (
                    f"Шаблоны проверены: {template_review.get('checked_templates', 0)}. "
                    "Критичных языковых правок в шаблонах не понадобилось."
                )
            _save_generator_state(state, job_id)

        remaining_payloads = [payload for payload in payloads if payload[0] not in completed_result_indices]
        render_started = perf_counter()

        if resume_stage == "render_docx" and ENABLE_CASE_AGENT:
            for payload in remaining_payloads:
                _refresh_generator_stop_flag(state, job_id)
                if state.get("stop_requested"):
                    raise GeneratorStopRequested("Генератор остановлен после завершения текущей строки.")
                result_index, _, row = payload
                state["current_client"] = _current_client_from_row(row, index=result_index + 1, total=len(payloads))
                _save_generator_state(state, job_id)
                try:
                    results[result_index] = process_generator_row(
                        payload,
                        output_dir=None if job_paths.uses_legacy_layout else job_paths.output_dir,
                        batch_docx_dir=None if job_paths.uses_legacy_layout else job_paths.batch_docx_dir,
                        templates_dir=None if job_paths.uses_legacy_layout else job_paths.templates_dir,
                        document_mode=effective_document_mode,
                        work_type=effective_work_type,
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
                completed_result_indices.add(result_index)
                state["completed_result_indices"] = sorted(completed_result_indices)
                state["results"] = results
                _save_generator_state(state, job_id)
        elif resume_stage == "render_docx":
            max_workers = max(1, min(DOCX_WORKERS, len(payloads)))
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                payload_iter = iter(remaining_payloads)
                future_map = {}

                def _submit_next() -> bool:
                    try:
                        payload = next(payload_iter)
                    except StopIteration:
                        return False
                    result_index, _, row = payload
                    state["current_client"] = _current_client_from_row(row, index=result_index + 1, total=len(payloads))
                    _save_generator_state(state, job_id)
                    future = executor.submit(
                        process_generator_row,
                        payload,
                        output_dir=None if job_paths.uses_legacy_layout else job_paths.output_dir,
                        batch_docx_dir=None if job_paths.uses_legacy_layout else job_paths.batch_docx_dir,
                        templates_dir=None if job_paths.uses_legacy_layout else job_paths.templates_dir,
                        document_mode=effective_document_mode,
                        work_type=effective_work_type,
                    )
                    future_map[future] = payload
                    return True

                for _ in range(min(max_workers, len(remaining_payloads))):
                    if not _submit_next():
                        break

                stop_submitting = False
                while future_map:
                    future = next(as_completed(list(future_map.keys())))
                    result_index, _, row = future_map[future]
                    future_map.pop(future, None)
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
                    completed_result_indices.add(result_index)
                    state["completed_result_indices"] = sorted(completed_result_indices)
                    state["results"] = results
                    _save_generator_state(state, job_id)
                    _refresh_generator_stop_flag(state, job_id)
                    if state.get("stop_requested"):
                        stop_submitting = True
                    while not stop_submitting and len(future_map) < max_workers and _submit_next():
                        pass
                if stop_submitting:
                    raise GeneratorStopRequested("Генератор остановлен после завершения текущего пакета строк.")

        if resume_stage == "render_docx":
            _record_generator_timing(
                state,
                job_id,
                "render_docx",
                render_started,
                total_rows=len(payloads),
                processed_rows=state.get("processed_rows", 0),
                workers=1 if ENABLE_CASE_AGENT else max(1, min(DOCX_WORKERS, len(payloads))),
                case_agent_enabled=ENABLE_CASE_AGENT,
            )

        state["results"] = results
        state["current_client"] = None
        staged_docx_total = sum(
            1
            for result in results
            for value in (result.get("generated_files") or {}).values()
            if isinstance(value, Path) and value.suffix.lower() == ".docx" and value.exists()
        )
        state["staged_docx_count"] = staged_docx_total
        state["pdf_total"] = staged_docx_total if create_pdf else 0
        state["pdf_processed"] = 0
        if create_pdf:
            state["stage"] = "convert_pdf"
            state["stage_text"] = "Сохраняю PDF для отправки."
        else:
            state["stage"] = "finalize_docx"
            state["stage_text"] = "Документы созданы. Передаю на проверку текста."
        _save_generator_state(state, job_id)

        pdf_target_dir = None if job_paths.uses_legacy_layout else job_paths.batch_pdf_dir
        pdf_progress = {"processed": 0}

        def _report_pdf_progress() -> None:
            pdf_progress["processed"] += 1
            state["pdf_processed"] = min(pdf_progress["processed"], staged_docx_total)
            _save_generator_state(state, job_id)

        finalize_started = perf_counter()
        finalize_generated_files(
            results,
            batch_pdf_dir=pdf_target_dir,
            progress_callback=_report_pdf_progress,
            should_stop=lambda: bool(_refresh_generator_stop_flag(state, job_id).get("stop_requested")),
            chunk_size=PDF_CHUNK_SIZE,
            worker_count=PDF_WORKERS,
            create_pdf=create_pdf,
        )
        _record_generator_timing(
            state,
            job_id,
            "convert_pdf" if create_pdf else "finalize_docx",
            finalize_started,
            total_documents=staged_docx_total,
            processed_pdf=state.get("pdf_processed", 0),
            workers=PDF_WORKERS,
            chunk_size=PDF_CHUNK_SIZE,
            create_pdf=create_pdf,
        )
        if create_pdf:
            state["stage"] = "finalize_output"
            state["stage_text"] = "Собираю результат."
        else:
            state["stage"] = "waiting_review"
            state["stage_text"] = "Документы созданы. Проверяю текст."
        _save_generator_state(state, job_id)
        inflection_log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
        postprocess_started = perf_counter()
        save_inflection_log(
            results,
            log_path=inflection_log_path,
        )
        inflection_summary = build_inflection_summary(results)
        review_handoffs = 0
        review_row_ids: list[str] = []
        for result in results:
            _validate_generated_result(result)
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
                    resolution_summary="Комплект документов готов.",
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
        _record_generator_timing(
            state,
            job_id,
            "postprocess_results",
            postprocess_started,
            total_results=len(results),
            review_handoffs=review_handoffs,
        )
        should_auto_run_philologist = settings.philologist_auto_run_enabled if auto_run_philologist is None else auto_run_philologist
        if review_row_ids and should_auto_run_philologist:
            from src.generator.philologist.philologist_agent import run_philologist

            philologist_started = perf_counter()
            philologist_result = run_philologist(ai_enabled=True, row_ids=review_row_ids, job_id=job_id)
            philologist_started_rows = len(review_row_ids)
            _record_generator_timing(
                state,
                job_id,
                "philologist_auto_run",
                philologist_started,
                row_count=philologist_started_rows,
                status=philologist_result.get("status") if isinstance(philologist_result, dict) else None,
            )

        state["results"] = results
        state["inflection_summary"] = inflection_summary
        state["ok_rows"] = sum(1 for item in results if item.get("status") == "ok")
        state["error_rows"] = sum(1 for item in results if item.get("status") == "error")
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
        state["status"] = "completed"
        state["stage"] = "completed"
        state["stage_text"] = "Генерация завершена."
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
    except GeneratorStopRequested as exc:
        state["status"] = "stopped"
        state["completed_at"] = None
        state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
        state["summary_text"] = str(exc) or "Генерация остановлена. Можно продолжить позже."
        state["results"] = results
        state["task_stats"] = count_tasks_for_agent("generator", job_id)
        state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
        state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
        _save_generator_state(state, job_id)
        return dict(state)
    except Exception as exc:
        logger.exception("generator_agent_failed")
        state["status"] = "error"
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
        state["error_rows"] = max(
            state.get("error_rows", 0),
            max(1, state.get("total_rows", 0) - state.get("ok_rows", 0)),
        )
        state["summary_text"] = f"Генерация завершилась с ошибкой: {exc}"
        state["task_stats"] = count_tasks_for_agent("generator", job_id)
        state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
        state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
        _save_generator_state(state, job_id)
        return dict(state)


def get_generator_status(job_id: str | None = None, *, include_details: bool = False) -> dict[str, Any]:
    state = _load_generator_state(job_id, include_details=include_details)
    job_paths = resolve_job_paths(job_id)
    output_dir = job_paths.output_dir if not job_paths.uses_legacy_layout else OUTPUT_DIR
    batch_docx_dir = job_paths.batch_docx_dir
    batch_pdf_dir = job_paths.batch_pdf_dir if not job_paths.uses_legacy_layout else BATCH_PDF_DIR
    is_running = state.get("status") == "running"
    if is_running:
        staged_docx_count = int(state.get("staged_docx_count") or 0)
        staged_pdf_count = int(state.get("staged_pdf_count") or 0)
        output_file_count = int(state.get("output_file_count") or 0)
    else:
        staged_docx_count = (
            _cached_file_count(batch_docx_dir, "*.docx")
            if batch_docx_dir.exists()
            else int(state.get("staged_docx_count") or 0)
        )
        staged_pdf_count = (
            _cached_file_count(batch_pdf_dir, "*.pdf")
            if batch_pdf_dir.exists()
            else int(state.get("staged_pdf_count") or 0)
        )
        output_file_count = _cached_file_count(output_dir, "*", recursive=True) if output_dir.exists() else 0
    state["staged_docx_count"] = staged_docx_count
    state["staged_pdf_count"] = staged_pdf_count
    state["pdf_total"] = int(state.get("pdf_total") or 0)
    state["pdf_processed"] = max(int(state.get("pdf_processed") or 0), staged_pdf_count)
    state["output_file_count"] = output_file_count
    if state.get("status") == "running" and staged_docx_count and not state.get("stage"):
        state["stage"] = "finalize_output"
        state["stage_text"] = "Собираю результат."
    state["task_stats"] = count_tasks_for_agent("generator", job_id)
    state["tasks"] = get_tasks_for_agent("generator", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="generator", limit=20, job_id=job_id)
    return state


def _safe_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _current_client_from_row(row: dict[str, Any], *, index: int, total: int) -> dict[str, Any]:
    label = (
        _safe_id(row.get("MUN_NAME"))
        or _safe_id(row.get("ADM_NAME"))
        or _safe_id(row.get("MUN_R_NAME"))
        or _safe_id(row.get("ID"))
        or f"строка {index}"
    )
    return {
        "index": index,
        "total": total,
        "row_id": _safe_id(row.get("ID")),
        "name": label,
    }
