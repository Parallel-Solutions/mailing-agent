from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets
import re
import json
from src.utils.logger import logger
from src.utils.config import settings
from src.web.documents_router import create_documents_router
from src.web.documents_service import (
    compact_documents_status,
    configure_documents_service,
    documents_agent_choose_reply,
    run_documents_pipeline_background,
)
from src.web.sender_router import create_sender_router
from src.web.sender_service import (
    compact_sender_status,
    configure_sender_service,
    prime_sender_checking_state,
    prime_sender_running_state,
    run_sender_background,
)
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Body, Form, BackgroundTasks
import shutil
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from time import perf_counter
import time
from fastapi.responses import StreamingResponse        # рядом с HTMLResponse
from fastapi.concurrency import run_in_threadpool
from src.parser_new.progress import subscribe as parser_progress_subscribe

app = FastAPI(title="Mailing Agent")
security = HTTPBasic()
_sender_threads: dict[str, threading.Thread] = {}
_sender_threads_lock = threading.Lock()
_philologist_threads: dict[str, threading.Thread] = {}
_philologist_threads_lock = threading.Lock()
_generator_threads: dict[str, threading.Thread] = {}
_generator_threads_lock = threading.Lock()
_documents_threads: dict[str, threading.Thread] = {}
_documents_threads_lock = threading.Lock()
_parser_verification_threads: dict[str, threading.Thread] = {}
_parser_verification_threads_lock = threading.Lock()
_output_archive_threads: dict[str, threading.Thread] = {}
_output_archive_threads_lock = threading.Lock()


@app.on_event("startup")
async def app_startup():
    return None


@app.on_event("shutdown")
async def app_shutdown():
    return None


def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, settings.app_username)
    ok_pass = secrets.compare_digest(credentials.password, settings.app_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _parse_optional_limit(payload: dict | None) -> int | None:
    if not payload:
        return None
    raw_value = payload.get("limit")
    if raw_value in (None, ""):
        return None
    text_value = str(raw_value).strip()
    if not text_value:
        return None
    return int(text_value)


def _prefer_existing_file(primary: Path, fallback: Path) -> Path:
    return primary if primary.exists() else fallback


def _format_upload_size_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes / (1024 * 1024):.0f} МБ"
    if max_bytes >= 1024:
        return f"{max_bytes / 1024:.0f} КБ"
    return f"{max_bytes} Б"


def _get_upload_size(upload: UploadFile) -> int | None:
    stream = getattr(upload, "file", None)
    if stream is None:
        return None
    try:
        current_position = stream.tell()
        stream.seek(0, 2)
        size = int(stream.tell())
        stream.seek(current_position)
        return size
    except Exception:
        return None


def _validate_uploaded_file(
    upload: UploadFile,
    *,
    allowed_extensions: tuple[str, ...],
    max_bytes: int,
    human_name: str,
) -> str:
    filename = Path(upload.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail=f"Не удалось определить имя файла для {human_name}.")
    extension = Path(filename).suffix.lower()
    if extension not in allowed_extensions:
        allowed_text = ", ".join(allowed_extensions)
        raise HTTPException(
            status_code=400,
            detail=f"Для {human_name} подходит только файл формата {allowed_text}.",
        )
    size = _get_upload_size(upload)
    if size is not None and size > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Файл слишком большой для {human_name}. "
                f"Максимальный размер: {_format_upload_size_limit(max_bytes)}."
            ),
        )
    return filename


_METADATA_CACHE_LOCK = threading.Lock()
_EXCEL_ROW_COUNT_CACHE: dict[str, dict[str, float | int | tuple[int, int]]] = {}
_TREE_FILE_COUNT_CACHE: dict[str, dict[str, float | int]] = {}
_JOB_HISTORY_ITEM_CACHE: dict[str, dict[str, object]] = {}
_JOB_HISTORY_SCAN_CACHE: dict[str, dict[str, object]] = {}
METADATA_CACHE_TTL_SECONDS = 10.0


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (int(stat.st_mtime_ns), int(stat.st_size))


def _cached_excel_row_count(path: Path) -> int:
    signature = _file_signature(path)
    if signature is None:
        return 0
    cache_key = str(path.resolve())
    with _METADATA_CACHE_LOCK:
        cached = _EXCEL_ROW_COUNT_CACHE.get(cache_key)
        if cached and cached.get("signature") == signature:
            return int(cached.get("count") or 0)
    try:
        workbook, _, rows = load_rows(path)
        count = len(rows)
        close = getattr(workbook, "close", None)
        if callable(close):
            close()
    except Exception:
        count = 0
    with _METADATA_CACHE_LOCK:
        _EXCEL_ROW_COUNT_CACHE[cache_key] = {"signature": signature, "count": count}
    return count


def _cached_tree_file_count(root: Path, pattern: str) -> int:
    if not root.exists():
        return 0
    cache_key = f"{root.resolve()}::{pattern}"
    now = time.monotonic()
    with _METADATA_CACHE_LOCK:
        cached = _TREE_FILE_COUNT_CACHE.get(cache_key)
        if cached and now - float(cached.get("cached_at") or 0.0) <= METADATA_CACHE_TTL_SECONDS:
            return int(cached.get("count") or 0)
    count = sum(1 for path in root.rglob(pattern) if path.is_file())
    with _METADATA_CACHE_LOCK:
        _TREE_FILE_COUNT_CACHE[cache_key] = {"cached_at": now, "count": count}
    return count


def _sender_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _philologist_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _generator_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _documents_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _parser_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _latest_matching_file(
    directories: list[Path],
    *,
    pattern: str,
    exclude_substring: str | None = None,
) -> Path | None:
    latest_path: Path | None = None
    latest_mtime = -1.0
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.glob(pattern):
            if exclude_substring and exclude_substring in path.name:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= latest_mtime:
                latest_mtime = mtime
                latest_path = path
    return latest_path


def _latest_tree_mtime(root: Path) -> float:
    latest_mtime = 0.0
    if not root.exists():
        return latest_mtime
    try:
        latest_mtime = root.stat().st_mtime
    except OSError:
        latest_mtime = 0.0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            latest_mtime = max(latest_mtime, path.stat().st_mtime)
        except OSError:
            continue
    return latest_mtime


def _job_state_dir(job_id: str | None) -> Path:
    paths = resolve_job_paths(job_id)
    state_dir = paths.root_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _resolve_cached_output_archive(job_id: str | None) -> tuple[Path, bool]:
    job_paths = resolve_job_paths(job_id)
    output_dir = job_paths.output_dir
    archive_path = _job_state_dir(job_id) / "output.zip"
    if not output_dir.exists():
        return archive_path, False
    if not archive_path.exists():
        return archive_path, False
    try:
        archive_mtime = archive_path.stat().st_mtime
    except OSError:
        return archive_path, False
    return archive_path, archive_mtime >= _latest_tree_mtime(output_dir)


def _build_output_archive(job_id: str | None) -> Path:
    output_dir = resolve_job_paths(job_id).output_dir
    archive_path, _ = _resolve_cached_output_archive(job_id)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temp_archive_path = archive_path.with_suffix(".tmp.zip")
    if temp_archive_path.exists():
        try:
            temp_archive_path.unlink()
        except OSError:
            pass
    with zipfile.ZipFile(temp_archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(output_dir))
    temp_archive_path.replace(archive_path)
    return archive_path


def _schedule_output_archive_build(job_id: str | None) -> None:
    key = _generator_job_key(job_id)
    with _output_archive_threads_lock:
        existing = _output_archive_threads.get(key)
        if existing and existing.is_alive():
            return

        def _run() -> None:
            try:
                output_dir = resolve_job_paths(job_id).output_dir
                if output_dir.exists() and any(output_dir.rglob("*.*")):
                    _build_output_archive(job_id)
            except Exception:
                logger.exception("output_archive_build_failed", job_id=job_id)
            finally:
                with _output_archive_threads_lock:
                    _output_archive_threads.pop(key, None)

        thread = threading.Thread(target=_run, daemon=True, name=f"archive-{key}")
        _output_archive_threads[key] = thread
        thread.start()


def _is_cache_fresh(cache_path: Path, source_paths: list[Path], *, max_age_seconds: int | None = None) -> bool:
    if not cache_path.exists():
        return False
    try:
        cache_mtime = cache_path.stat().st_mtime
    except OSError:
        return False
    if max_age_seconds is not None and (time.time() - cache_mtime) > max_age_seconds:
        return False
    for source_path in source_paths:
        if not source_path.exists():
            continue
        try:
            if source_path.stat().st_mtime > cache_mtime:
                return False
        except OSError:
            continue
    return True


def _get_sender_thread(job_id: str | None) -> threading.Thread | None:
    key = _sender_job_key(job_id)
    with _sender_threads_lock:
        thread = _sender_threads.get(key)
        if thread and not thread.is_alive():
            _sender_threads.pop(key, None)
            return None
        return thread


def _get_philologist_thread(job_id: str | None) -> threading.Thread | None:
    key = _philologist_job_key(job_id)
    with _philologist_threads_lock:
        thread = _philologist_threads.get(key)
        if thread and not thread.is_alive():
            _philologist_threads.pop(key, None)
            return None
        return thread


def _get_generator_thread(job_id: str | None) -> threading.Thread | None:
    key = _generator_job_key(job_id)
    with _generator_threads_lock:
        thread = _generator_threads.get(key)
        if thread and not thread.is_alive():
            _generator_threads.pop(key, None)
            return None
        return thread


def _get_documents_thread(job_id: str | None) -> threading.Thread | None:
    key = _documents_job_key(job_id)
    with _documents_threads_lock:
        thread = _documents_threads.get(key)
        if thread and not thread.is_alive():
            _documents_threads.pop(key, None)
            return None
        return thread


def _get_parser_verification_thread(job_id: str | None) -> threading.Thread | None:
    key = _parser_job_key(job_id)
    with _parser_verification_threads_lock:
        process = _parser_verification_threads.get(key)
        if process and not process.is_alive():
            _parser_verification_threads.pop(key, None)
            return None
        return process


def _register_sender_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _sender_threads_lock:
        _sender_threads[_sender_job_key(job_id)] = thread


def _register_philologist_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _philologist_threads_lock:
        _philologist_threads[_philologist_job_key(job_id)] = thread


def _register_generator_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _generator_threads_lock:
        _generator_threads[_generator_job_key(job_id)] = thread


def _register_documents_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _documents_threads_lock:
        _documents_threads[_documents_job_key(job_id)] = thread


def _start_sender_thread_if_absent(
    job_id: str | None,
    *,
    target,
    kwargs: dict | None = None,
    name: str | None = None,
    before_start=None,
) -> tuple[threading.Thread, bool]:
    with _sender_threads_lock:
        key = _sender_job_key(job_id)
        existing = _sender_threads.get(key)
        if existing and existing.is_alive():
            return existing, False
        if existing and not existing.is_alive():
            _sender_threads.pop(key, None)
        if before_start is not None:
            before_start()
        thread = threading.Thread(
            target=target,
            kwargs=kwargs or {},
            daemon=True,
            name=name or f"sender-{key}",
        )
        _sender_threads[key] = thread
        thread.start()
        return thread, True


def _start_documents_thread_if_absent(
    job_id: str | None,
    *,
    target,
    kwargs: dict | None = None,
    name: str | None = None,
) -> tuple[threading.Thread, bool]:
    with _documents_threads_lock:
        key = _documents_job_key(job_id)
        existing = _documents_threads.get(key)
        if existing and existing.is_alive():
            return existing, False
        if existing and not existing.is_alive():
            _documents_threads.pop(key, None)
        thread = threading.Thread(
            target=target,
            kwargs=kwargs or {},
            daemon=True,
            name=name or f"documents-{key}",
        )
        _documents_threads[key] = thread
        thread.start()
        return thread, True


def _unregister_sender_thread(job_id: str | None) -> None:
    with _sender_threads_lock:
        _sender_threads.pop(_sender_job_key(job_id), None)


def _unregister_documents_thread(job_id: str | None) -> None:
    with _documents_threads_lock:
        _documents_threads.pop(_documents_job_key(job_id), None)


def _register_parser_verification_thread(job_id: str | None, process: threading.Thread) -> None:
    with _parser_verification_threads_lock:
        _parser_verification_threads[_parser_job_key(job_id)] = process


def _compact_philologist_status(state: dict) -> dict:
    documents = state.get("documents") or []
    document_count = len(documents) if isinstance(documents, list) and documents else int(
        state.get("documents_count") or state.get("document_count") or state.get("processed_documents") or 0
    )
    tool_trace = state.get("tool_trace") or []
    tool_trace_count = len(tool_trace) if isinstance(tool_trace, list) and tool_trace else int(
        state.get("tool_trace_count") or 0
    )
    context_review = state.get("inflection_context_review") or {}
    context_corrections = state.get("inflection_context_corrections") or {}
    current_document = state.get("current_document")
    if isinstance(current_document, dict):
        current_document = {
            "index": current_document.get("index"),
            "total": current_document.get("total"),
            "name": current_document.get("name"),
        }

    return {
        "status": state.get("status", "idle"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "elapsed_seconds": state.get("elapsed_seconds"),
        "total_documents": state.get("total_documents", 0),
        "processed_documents": state.get("processed_documents", 0),
        "fixed_documents": state.get("fixed_documents", 0),
        "documents_with_issues": state.get("documents_with_issues", 0),
        "summary_text": state.get("summary_text", ""),
        "mode": state.get("mode", ""),
        "ai_review_enabled": state.get("ai_review_enabled", False),
        "inflection_log_count": state.get("inflection_log_count", 0),
        "current_document": current_document,
        "document_count": document_count,
        "tool_trace_count": tool_trace_count,
        "inflection_context_review": {
            "enabled": context_review.get("enabled", False),
            "selected_count": context_review.get("selected_count", 0),
            "checked_count": context_review.get("checked_count", 0),
            "error": context_review.get("error", ""),
        },
        "inflection_context_corrections": {
            "applied_count": context_corrections.get("applied_count", 0),
        },
        "task_stats": state.get("task_stats", {}),
        "recent_events": (state.get("recent_events") or [])[:5],
    }


def _compact_generator_status(state: dict) -> dict:
    inflection_summary = state.get("inflection_summary", {})
    if isinstance(inflection_summary, dict) and inflection_summary.get("sample_warnings"):
        inflection_summary = dict(inflection_summary)
        inflection_summary["sample_warnings"] = list(inflection_summary.get("sample_warnings") or [])[:3]
    return {
        "status": state.get("status", "idle"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "elapsed_seconds": state.get("elapsed_seconds"),
        "total_rows": state.get("total_rows", 0),
        "processed_rows": state.get("processed_rows", 0),
        "ok_rows": state.get("ok_rows", 0),
        "error_rows": state.get("error_rows", 0),
        "stage": state.get("stage", "idle"),
        "stage_text": state.get("stage_text", ""),
        "summary_text": state.get("summary_text", ""),
        "staged_docx_count": state.get("staged_docx_count", 0),
        "staged_pdf_count": state.get("staged_pdf_count", 0),
        "pdf_total": state.get("pdf_total", 0),
        "pdf_processed": state.get("pdf_processed", 0),
        "output_file_count": state.get("output_file_count", 0),
        "inflection_summary": inflection_summary if isinstance(inflection_summary, dict) else {},
        "template_review": state.get("template_review", {}),
        "stop_requested": state.get("stop_requested", False),
        "task_stats": state.get("task_stats", {}),
        "recent_events": (state.get("recent_events") or [])[:5],
    }


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _read_state_json(path: Path) -> dict:
    try:
        if not path.exists() or not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _state_file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


def _format_history_time(timestamp: float) -> str:
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _job_history_mtime(job_dir: Path) -> float:
    state_dir = job_dir / "state"
    candidates = [
        job_dir,
        job_dir / "input" / "data.xlsx",
        state_dir / "parser.json",
        state_dir / "generator.json",
        state_dir / "philologist.json",
        state_dir / "sender.json",
        state_dir / "unisender_go_events.jsonl",
        job_dir / "sent_mail_log.jsonl",
    ]
    return max((_state_file_mtime(path) for path in candidates), default=0.0)


def _job_history_sender_hint(job_dir: Path) -> bool:
    sent_log_path = job_dir / "sent_mail_log.jsonl"
    try:
        if sent_log_path.exists() and sent_log_path.stat().st_size > 0:
            return True
    except OSError:
        pass

    sender_state = _read_state_json(job_dir / "state" / "sender.json")
    if not sender_state:
        return False

    sender_mode = str(sender_state.get("mode") or "")
    sender_status = str(sender_state.get("status") or "")
    sender_stats = sender_state.get("stats") if isinstance(sender_state.get("stats"), dict) else {}
    sent_rows = max(_safe_int(sender_stats.get("sent")), _safe_int(sender_state.get("sent_rows")))
    error_rows = max(_safe_int(sender_stats.get("error")), _safe_int(sender_state.get("error_rows")))
    total_rows = max(_safe_int(sender_stats.get("total")), _safe_int(sender_state.get("total_rows")))
    pending_rows = max(0, total_rows - sent_rows - error_rows) if total_rows else 0

    if sender_mode == "send":
        return True
    if sent_rows > 0 or error_rows > 0:
        return True
    if sender_status == "running" and pending_rows > 0:
        return True
    return False


def _job_history_candidate(job_dir: Path) -> tuple[float, bool]:
    state_dir = job_dir / "state"
    sender_path = state_dir / "sender.json"
    sent_log_path = job_dir / "sent_mail_log.jsonl"
    cache_key = str(job_dir.resolve())
    root_mtime = _state_file_mtime(job_dir)
    state_mtime = _state_file_mtime(state_dir)
    sender_mtime = _state_file_mtime(sender_path)
    sent_log_mtime = _state_file_mtime(sent_log_path)

    with _METADATA_CACHE_LOCK:
        cached = _JOB_HISTORY_SCAN_CACHE.get(cache_key)
        if (
            cached
            and float(cached.get("root_mtime") or 0.0) == float(root_mtime)
            and float(cached.get("state_mtime") or 0.0) == float(state_mtime)
            and float(cached.get("sender_mtime") or 0.0) == float(sender_mtime)
            and float(cached.get("sent_log_mtime") or 0.0) == float(sent_log_mtime)
        ):
            return (
                float(cached.get("updated_at_ts") or 0.0),
                bool(cached.get("is_mailing_hint")),
            )

    updated_at_ts = _job_history_mtime(job_dir)
    is_mailing_hint = _job_history_sender_hint(job_dir)
    with _METADATA_CACHE_LOCK:
        _JOB_HISTORY_SCAN_CACHE[cache_key] = {
            "root_mtime": float(root_mtime),
            "state_mtime": float(state_mtime),
            "sender_mtime": float(sender_mtime),
            "sent_log_mtime": float(sent_log_mtime),
            "updated_at_ts": float(updated_at_ts),
            "is_mailing_hint": bool(is_mailing_hint),
        }
    return updated_at_ts, is_mailing_hint


def _job_history_status(
    *,
    parser_state: dict,
    generator_state: dict,
    philologist_state: dict,
    sender_state: dict,
    data_exists: bool,
) -> tuple[str, str]:
    sender_status = str(sender_state.get("status") or "idle")
    sender_mode = str(sender_state.get("mode") or "")
    sent_rows = _safe_int(sender_state.get("sent_rows") or (sender_state.get("stats") or {}).get("sent"))
    error_rows = _safe_int(sender_state.get("error_rows") or (sender_state.get("stats") or {}).get("error"))
    ready_rows = _safe_int(sender_state.get("ready_rows"))
    if sender_status == "running":
        return ("Отправка идёт" if sender_mode == "send" else "Проверка отправки", "progress")
    if sent_rows > 0 or sender_mode == "send":
        return ("Есть ошибки отправки" if error_rows else "Отправка завершена", "error" if error_rows else "ok")
    if ready_rows > 0 or (sender_status == "completed" and sender_mode == "dry_run"):
        return ("Проверка отправки готова", "ok")

    philologist_status = str(philologist_state.get("status") or "idle")
    if philologist_status == "running":
        return ("Проверка документов идёт", "progress")
    if philologist_status == "stopped":
        return ("Документы остановлены", "wait")
    if philologist_status == "completed":
        return ("Документы готовы", "ok")

    generator_status = str(generator_state.get("status") or "idle")
    if generator_status == "running":
        return ("Документы готовятся", "progress")
    if generator_status == "completed":
        return ("Документы созданы", "ok")
    if generator_status == "error":
        return ("Ошибка документов", "error")

    parser_status = str((parser_state.get("municipality_name_verification_state") or {}).get("status") or parser_state.get("status") or "idle")
    if parser_status == "running":
        return ("Таблица проверяется", "progress")
    if data_exists:
        return ("Таблица загружена", "wait")
    return ("Черновик", "idle")


def _build_job_history_item(job_dir: Path, updated_at_ts: float) -> dict:
    cache_key = str(job_dir.resolve())
    with _METADATA_CACHE_LOCK:
        cached = _JOB_HISTORY_ITEM_CACHE.get(cache_key)
        if cached and float(cached.get("updated_at_ts") or 0.0) == float(updated_at_ts):
            cached_item = cached.get("item")
            if isinstance(cached_item, dict):
                return dict(cached_item)

    job_id = job_dir.name
    paths = resolve_job_paths(job_id)
    state_dir = job_dir / "state"
    parser_state = _read_state_json(state_dir / "parser.json")
    generator_state = _read_state_json(state_dir / "generator.json")
    philologist_state = _read_state_json(state_dir / "philologist.json")
    sender_state = _read_state_json(state_dir / "sender.json")

    sender_stats = sender_state.get("stats") if isinstance(sender_state.get("stats"), dict) else {}
    total_rows = max(
        _safe_int(sender_stats.get("total")),
        _safe_int(sender_state.get("total_rows")),
        _safe_int(generator_state.get("total_rows")),
        _safe_int(parser_state.get("row_count")),
    )
    sent_rows = max(_safe_int(sender_stats.get("sent")), _safe_int(sender_state.get("sent_rows")))
    error_rows = max(_safe_int(sender_stats.get("error")), _safe_int(sender_state.get("error_rows")))
    pending_rows = max(0, total_rows - sent_rows - error_rows) if total_rows else 0
    ready_rows = _safe_int(sender_state.get("ready_rows"))
    reviewed_documents = _safe_int(philologist_state.get("processed_documents"))
    total_documents = _safe_int(philologist_state.get("total_documents"))
    generated_rows = max(_safe_int(generator_state.get("ok_rows")), _safe_int(generator_state.get("processed_rows")))
    label, tone = _job_history_status(
        parser_state=parser_state,
        generator_state=generator_state,
        philologist_state=philologist_state,
        sender_state=sender_state,
        data_exists=paths.data_xlsx.exists(),
    )

    item = {
        "job_id": job_id,
        "updated_at": _format_history_time(updated_at_ts),
        "status_label": label,
        "status_tone": tone,
        "total_rows": total_rows,
        "generated_rows": generated_rows,
        "reviewed_documents": reviewed_documents,
        "total_documents": total_documents,
        "ready_rows": ready_rows,
        "sent_rows": sent_rows,
        "error_rows": error_rows,
        "pending_rows": pending_rows,
        "has_data": paths.data_xlsx.exists(),
        "has_output": paths.output_dir.exists(),
        "sender_status": sender_state.get("status", "idle"),
        "sender_mode": sender_state.get("mode", "dry_run"),
    }
    with _METADATA_CACHE_LOCK:
        _JOB_HISTORY_ITEM_CACHE[cache_key] = {
            "updated_at_ts": float(updated_at_ts),
            "item": dict(item),
        }
    return item


def _job_history_is_mailing_session(item: dict) -> bool:
    sender_mode = str(item.get("sender_mode") or "")
    sender_status = str(item.get("sender_status") or "")
    sent_rows = _safe_int(item.get("sent_rows"))
    error_rows = _safe_int(item.get("error_rows"))
    pending_rows = _safe_int(item.get("pending_rows"))

    if sender_mode == "send":
        return True
    if sent_rows > 0 or error_rows > 0:
        return True
    if sender_status == "running" and pending_rows > 0:
        return True
    return False


def _run_philologist_background(*, ai_enabled: bool, job_id: str | None, mode: str | None) -> None:
    try:
        result = run_philologist(ai_enabled=ai_enabled, job_id=job_id, mode=mode)
        if isinstance(result, dict) and result.get("status") == "completed":
            _schedule_output_archive_build(job_id)
    except Exception as exc:
        logger.exception("philologist_background_failed", job_id=job_id)
        state = _load_philologist_state(job_id)
        state["status"] = "error"
        state["completed_at"] = None
        state["summary_text"] = f"Агент-филолог остановился с ошибкой: {type(exc).__name__}: {exc}"
        _save_philologist_state(state, job_id)
    finally:
        with _philologist_threads_lock:
            _philologist_threads.pop(_philologist_job_key(job_id), None)


def _run_generator_background(*, xlsx_path: Path, job_id: str | None) -> None:
    try:
        result = run_generator_agent(xlsx_path=xlsx_path, job_id=job_id)
        if isinstance(result, dict) and result.get("status") == "completed":
            _schedule_output_archive_build(job_id)
    except Exception as exc:
        logger.exception("generator_background_failed", job_id=job_id)
        state = _load_generator_state(job_id)
        state["status"] = "error"
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["summary_text"] = f"Генератор остановился с ошибкой: {type(exc).__name__}: {exc}"
        _save_generator_state(state, job_id)
    finally:
        with _generator_threads_lock:
            _generator_threads.pop(_generator_job_key(job_id), None)


def _run_parser_verification_background(*, job_id: str | None, source: str) -> None:
    logger.info("parser_upload_verification_background_started", job_id=job_id, source=source)
    try:
        result = run_parser_municipality_verification(job_id, source=source)
        logger.info(
            "parser_upload_verification_background_completed",
            job_id=job_id,
            source=source,
            status=result.get("status"),
            total_rows=result.get("total_rows"),
            updated_rows=result.get("updated_rows"),
            verified_rows=result.get("verified_rows"),
            kept_rows=result.get("kept_rows"),
            duration_seconds=(result.get("timings") or {}).get("overall_seconds"),
        )
    except Exception:
        logger.exception("parser_upload_verification_background_failed", job_id=job_id, source=source)
    finally:
        with _parser_verification_threads_lock:
            current = _parser_verification_threads.get(_parser_job_key(job_id))
            if current is threading.current_thread():
                _parser_verification_threads.pop(_parser_job_key(job_id), None)


def _start_parser_verification_process(*, job_id: str | None, filename: str, source: str = "upload") -> None:
    started_at = perf_counter()
    existing_thread = _get_parser_verification_thread(job_id)
    if existing_thread is not None:
        logger.info(
            "upload_data_verification_already_running",
            filename=filename,
            job_id=job_id,
            worker_name=existing_thread.name,
        )
        return

    verification_thread = threading.Thread(
        target=_run_parser_verification_background,
        kwargs={"job_id": job_id, "source": source},
        daemon=True,
        name=f"parser-verify-{_parser_job_key(job_id)}",
    )
    verification_thread.start()
    _register_parser_verification_thread(job_id, verification_thread)
    logger.info(
        "upload_data_verification_scheduled",
        filename=filename,
        job_id=job_id,
        worker_name=verification_thread.name,
        schedule_seconds=round(perf_counter() - started_at, 3),
    )


def _prime_philologist_running_state(job_id: str | None, mode: str | None) -> dict:
    paths = resolve_job_paths(job_id)
    output_dir = paths.output_dir
    docx_count = len(list(output_dir.rglob("*.docx"))) if output_dir.exists() else 0
    state = _load_philologist_state(job_id)
    saved_processed = int(state.get("processed_documents") or 0)
    saved_documents = state.get("documents") if isinstance(state.get("documents"), list) else []
    has_resume_checkpoint = saved_processed > 0 or bool(saved_documents)
    if str(state.get("status") or "") == "stopped" or has_resume_checkpoint:
        state["status"] = "running"
        state["completed_at"] = None
        state["mode"] = mode or state.get("mode") or "fast"
        state["resume_from_stopped"] = True
        state["summary_text"] = "Продолжаю работу агента-филолога с сохраненного места."
    else:
        state["status"] = "running"
        state["started_at"] = None
        state["completed_at"] = None
        state["total_documents"] = docx_count
        state["processed_documents"] = 0
        state["fixed_documents"] = 0
        state["documents_with_issues"] = 0
        state["mode"] = mode or "fast"
        state["summary_text"] = "Агент-филолог запущен в фоне и готовит документы к проверке."
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _save_philologist_state(state, job_id)
    return state


@app.get("/", response_class=HTMLResponse)
async def index(username: str = Depends(check_auth)):
    return Path("templates/index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def app_status(username: str = Depends(check_auth)):
    return {"status": "ok", "message": "Сервер работает"}


@app.post("/api/jobs")
async def create_job(username: str = Depends(check_auth)):
    job_id = create_job_id()
    paths = resolve_job_paths(job_id)
    paths.ensure_dirs()
    return {"status": "ok", "job_id": job_id}


@app.get("/api/jobs/history")
async def jobs_history(limit: int = 40, username: str = Depends(check_auth)):
    safe_limit = max(1, min(int(limit or 40), 200))
    if not JOBS_DIR.exists():
        return {"status": "ok", "result": {"jobs": []}}

    candidates: list[tuple[float, Path]] = []
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir() or not job_dir.name.startswith("job-"):
            continue
        updated_at, is_mailing_hint = _job_history_candidate(job_dir)
        if not is_mailing_hint:
            continue
        candidates.append((updated_at, job_dir))

    candidates.sort(key=lambda item: item[0], reverse=True)
    jobs: list[dict] = []
    for updated_at, job_dir in candidates:
        try:
            item = _build_job_history_item(job_dir, updated_at)
        except Exception:
            logger.exception("jobs_history_item_failed", job_dir=str(job_dir))
            continue
        if not _job_history_is_mailing_session(item):
            continue
        jobs.append(item)
        if len(jobs) >= safe_limit:
            break
    return {"status": "ok", "result": {"jobs": jobs}}


@app.get("/api/jobs/latest-data")
async def latest_data_job(after: float = 0.0, username: str = Depends(check_auth)):
    latest: tuple[float, str, Path] | None = None
    if JOBS_DIR.exists():
        for job_dir in JOBS_DIR.iterdir():
            if not job_dir.is_dir() or not job_dir.name.startswith("job-"):
                continue
            data_path = resolve_job_paths(job_dir.name).data_xlsx
            updated_at = _state_file_mtime(data_path)
            if updated_at <= 0:
                continue
            if after > 0 and updated_at < after:
                continue
            if latest is None or updated_at > latest[0]:
                latest = (updated_at, job_dir.name, data_path)

    legacy_data_path = resolve_job_paths(None).data_xlsx
    legacy_updated_at = _state_file_mtime(legacy_data_path)
    if legacy_updated_at > 0 and (after <= 0 or legacy_updated_at >= after):
        if latest is None or legacy_updated_at > latest[0]:
            latest = (legacy_updated_at, "", legacy_data_path)

    if latest is None:
        return {"status": "ok", "result": {"found": False}}

    updated_at, job_id, data_path = latest
    return {
        "status": "ok",
        "result": {
            "found": True,
            "job_id": job_id,
            "updated_at": _format_history_time(updated_at),
            "row_count": _cached_excel_row_count(data_path),
        },
    }


def _clone_job_templates_if_present(source_job_id: str | None, target_job_id: str | None) -> None:
    source_paths = resolve_job_paths(source_job_id)
    target_paths = resolve_job_paths(target_job_id)
    source_dir = source_paths.templates_dir
    target_dir = target_paths.templates_dir
    if not source_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_dir.iterdir():
        if not source_path.is_file():
            continue
        shutil.copy2(source_path, target_dir / source_path.name)


@app.post("/api/upload/data")
async def upload_data(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    job_id: str | None = Form(default=None),
    username: str = Depends(check_auth),
):
    request_started = perf_counter()
    safe_filename = _validate_uploaded_file(
        file,
        allowed_extensions=(".xlsx",),
        max_bytes=settings.upload_data_max_bytes,
        human_name="таблицы",
    )
    logger.info("upload_data_request_started", filename=safe_filename, requested_job_id=job_id)
    paths = resolve_job_paths(job_id)
    if not paths.uses_legacy_layout and paths.data_xlsx.exists():
        fresh_job_id = create_job_id()
        fresh_paths = resolve_job_paths(fresh_job_id)
        fresh_paths.ensure_dirs()
        _clone_job_templates_if_present(paths.job_id, fresh_job_id)
        paths = fresh_paths
    paths.ensure_dirs()
    dest = paths.data_xlsx
    dest.parent.mkdir(parents=True, exist_ok=True)
    file_save_started = perf_counter()
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    file_save_seconds = round(perf_counter() - file_save_started, 3)
    logger.info(
        "upload_data_file_saved",
        filename=safe_filename,
        job_id=paths.job_id,
        file_save_seconds=file_save_seconds,
        request_seconds=round(perf_counter() - request_started, 3),
    )

    background_tasks.add_task(
        _start_parser_verification_process,
        job_id=paths.job_id,
        filename=safe_filename,
        source="upload",
    )

    return {
        "status": "ok",
        "filename": safe_filename,
        "job_id": paths.job_id,
        "data_download_url": f"/api/download/data-xlsx?job_id={paths.job_id}",
        "verification_background": True,
        "municipality_name_verification_state": {
            "status": "running",
            "source": "upload",
            "summary_text": "Файл загружен. Идёт проверка официальных названий МО после загрузки таблицы.",
        },
        "timings": {
            "file_save_seconds": file_save_seconds,
            "request_seconds": round(perf_counter() - request_started, 3),
        },
    }


@app.get("/api/data/info")
async def data_info(job_id: str | None = None, username: str = Depends(check_auth)):
    data_path = _prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
    if not data_path.exists():
        return {"loaded": False, "total": 0}
    return {"loaded": True, "total": _cached_excel_row_count(data_path)}


def _build_job_readiness_result(job_id: str | None = None) -> dict:
    paths = resolve_job_paths(job_id)
    data_path = _prefer_existing_file(paths.data_xlsx, Path("data/data.xlsx"))
    row_count = 0
    if data_path.exists():
        row_count = _cached_excel_row_count(data_path)

    templates_dir = paths.templates_dir
    kp_template_loaded = (templates_dir / "kp_template_source.docx").exists()
    contract_template_loaded = (templates_dir / "contract_template_source.docx").exists()
    mail_template_loaded = any(
        (templates_dir / name).exists()
        for name in ("mail_template.docx", "mail_template.txt")
    )

    output_dir = paths.output_dir
    parser_state = get_parser_status(job_id)
    generator_state = get_generator_status(job_id)
    philologist_state = get_philologist_status(job_id, include_details=False)

    parser_verification_state = parser_state.get("municipality_name_verification_state") or {}
    parser_verification_result = parser_state.get("municipality_name_verification") or {}
    parser_verification_status = str(parser_verification_state.get("status") or "idle")
    parser_verification_completed = (
        parser_verification_status == "completed"
        or str(parser_verification_result.get("status") or "") == "ok"
    )
    parser_running = str(parser_state.get("status") or "") == "running" or parser_verification_status == "running"
    generator_status = str(generator_state.get("status") or "")
    philologist_status = str(philologist_state.get("status") or "")
    generator_running = generator_status == "running"
    philologist_running = philologist_status in {"running", "finalizing"}
    reviewed_documents = int(philologist_state.get("processed_documents") or 0)
    total_documents = int(philologist_state.get("total_documents") or 0)
    philologist_completed = philologist_status == "completed" or (
        total_documents > 0
        and reviewed_documents >= total_documents
        and philologist_status in {"running", "finalizing"}
    )
    documents_completed = generator_status == "completed" and philologist_completed
    output_docx_count = max(
        int(generator_state.get("staged_docx_count") or 0),
        int(philologist_state.get("total_documents") or 0),
    )
    if output_docx_count <= 0:
        output_docx_count = _cached_tree_file_count(output_dir, "*.docx")
    output_pdf_count = int(generator_state.get("staged_pdf_count") or 0)
    if output_pdf_count <= 0:
        output_pdf_count = _cached_tree_file_count(output_dir, "*.pdf")

    generator_reasons: list[str] = []
    philologist_reasons: list[str] = []
    sender_reasons: list[str] = []

    if not data_path.exists():
        generator_reasons.append("Не загружен data.xlsx.")
        sender_reasons.append("Не загружен data.xlsx.")
    elif row_count <= 0:
        generator_reasons.append("В data.xlsx нет строк для обработки.")
        sender_reasons.append("В data.xlsx нет строк для отправки.")

    if not kp_template_loaded:
        generator_reasons.append("Не загружен шаблон КП.")
    if not contract_template_loaded:
        generator_reasons.append("Не загружен шаблон договора.")
    if parser_running:
        generator_reasons.append("Парсер ещё работает.")
    if data_path.exists() and row_count > 0 and not parser_verification_completed:
        generator_reasons.append("Таблица ещё не проверена.")

    if output_docx_count <= 0:
        philologist_reasons.append("Нет готовых DOCX-документов.")
    if generator_running:
        philologist_reasons.append("Генератор ещё работает.")

    if output_pdf_count <= 0 and not documents_completed:
        sender_reasons.append("Нет готовых PDF-вложений.")
    if generator_running and not documents_completed:
        sender_reasons.append("Генератор ещё работает.")
    if philologist_running and not documents_completed:
        sender_reasons.append("Филолог ещё работает.")

    if job_id:
        base_path = paths.base_xlsx
    else:
        base_path = _prefer_existing_file(paths.base_xlsx, Path("service_docs/base.xlsx"))

    parser_total = _cached_excel_row_count(base_path) if base_path.exists() else 0
    generator_total = max(
        row_count,
        int(generator_state.get("total_rows", 0) or 0),
    )
    if generator_total <= 0:
        philologist_total = int(philologist_state.get("total_documents", 0) or 0)
        if philologist_total > 0:
            generator_total = max(generator_total, philologist_total // 2)
    sender_state = get_sender_status(job_id)
    sender_total = max(
        generator_total,
        int(sender_state.get("total_rows", 0) or 0),
        int((sender_state.get("stats") or {}).get("total", 0) or 0),
    )

    return {
        "data_loaded": data_path.exists(),
        "row_count": row_count,
        "kp_template_loaded": kp_template_loaded,
        "contract_template_loaded": contract_template_loaded,
        "mail_template_loaded": mail_template_loaded,
        "output_docx_count": output_docx_count,
        "output_pdf_count": output_pdf_count,
        "parser_running": parser_running,
        "generator_running": generator_running,
        "philologist_running": philologist_running,
        "generator_ready": not generator_reasons,
        "philologist_ready": not philologist_reasons,
        "sender_ready": not sender_reasons,
        "generator_reason": " ".join(generator_reasons).strip(),
        "philologist_reason": " ".join(philologist_reasons).strip(),
        "sender_reason": " ".join(sender_reasons).strip(),
        "counts": {
            "parser_total": parser_total,
            "generator_total": generator_total,
            "sender_total": sender_total,
        },
    }


@app.get("/api/job/readiness")
async def job_readiness(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": _build_job_readiness_result(job_id)}


@app.post("/api/data/verify-municipality-names")
async def data_verify_municipality_names(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    job_id = (payload or {}).get("job_id")
    data_path = _prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="Файл data.xlsx не найден.")
    return {
        "status": "ok",
        "result": run_parser_municipality_verification(job_id, source="api"),
    }


@app.post("/api/upload/template")
async def upload_template(
    file: UploadFile = File(...),
    job_id: str | None = Form(default=None),
    template_kind: str | None = Form(default=None),
    username: str = Depends(check_auth),
):
    paths = resolve_job_paths(job_id)
    paths.ensure_dirs()
    templates_dir = paths.templates_dir
    templates_dir.mkdir(exist_ok=True)
    kind = (template_kind or "").strip().lower()
    allowed_extensions = (".docx", ".txt") if kind == "mail" else (".docx",)
    human_name = (
        "почтового шаблона"
        if kind == "mail"
        else "шаблона КП"
        if kind == "kp"
        else "шаблона договора"
        if kind == "contract"
        else "шаблона"
    )
    original_name = _validate_uploaded_file(
        file,
        allowed_extensions=allowed_extensions,
        max_bytes=settings.upload_template_max_bytes,
        human_name=human_name,
    )
    if kind == "mail":
        for stale_name in ("mail_template.txt", "mail_template.docx"):
            stale_path = templates_dir / stale_name
            if stale_path.exists():
                stale_path.unlink()
        dest = templates_dir / ("mail_template.docx" if original_name.lower().endswith(".docx") else "mail_template.txt")
    elif kind == "kp":
        dest = templates_dir / "kp_template_source.docx"
    elif kind == "contract":
        dest = templates_dir / "contract_template_source.docx"
    else:
        raise HTTPException(status_code=400, detail="Не указан тип шаблона.")
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "status": "ok",
        "filename": file.filename,
        "stored_as": dest.name,
        "job_id": paths.job_id,
    }

from src.generator.generation.excel_io import load_rows
from src.generator.generation.transforms import build_document_context
from src.generator.generation.document_builder import cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.generation.config_generator import (
    BATCH_PDF_DIR,
    DOCX_WORKERS,
    ONLYOFFICE_PUBLIC_FILES_DIR,
    START_OUTGOING_NUMBER,
    WEB_CASE_AGENT_MAX_WORKERS,
)
from src.generator.inflection.ai_case_agent import (
    ENABLE_CASE_AGENT,
    CASE_AGENT_ONLY_SUSPICIOUS,
    apply_case_agent_result,
    run_case_validation_agent,
)
from src.generator.philologist.philologist_agent import (
    _load_philologist_state,
    _save_philologist_state,
    chat_with_philologist,
    clear_philologist_stop_request,
    get_philologist_status,
    request_philologist_stop,
    run_philologist,
)
from src.generator.delivery.sender_agent import (
    _collect_excel_stats,
    _load_sender_state,
    _save_sender_state,
    chat_with_sender,
    clear_sender_stop_request,
    get_sender_status,
    get_unisender_history,
    preview_recipients,
    request_sender_stop,
    run_sender,
)
from src.generator.delivery.sender_report import (
    build_unisender_delivery_analytics,
    build_unisender_delivery_report_xlsx,
    unisender_delivery_report_has_data,
)
from src.generator.delivery.unisender_go_events import append_unisender_go_events
from src.generator.orchestration.parser_agent import (
    chat_with_parser,
    format_municipality_verification_for_chat,
    get_parser_status,
    mark_municipality_verification_failed,
    run_parser_agent,
    run_parser_municipality_verification,
)
from src.generator.orchestration.orchestrator_agent import (
    chat_with_orchestrator,
    get_orchestrator_status,
)
from src.generator.orchestration.autonomous_worker import (
    get_autonomous_worker_state,
    start_autonomous_worker,
    stop_autonomous_worker,
)
from src.generator.knowledge.agent_memory import (
    build_agent_report,
    build_quarantine_items,
    build_learning_candidates,
    get_agent_memory_csv_path,
    get_agent_quarantine_csv_path,
    get_agent_report_path,
    save_agent_report,
    save_learning_memory_csv,
    save_quarantine_csv,
)
from src.generator.knowledge.correction_report import (
    build_correction_report_xlsx,
    correction_report_has_data,
)
from src.generator.case_engine.overrides import upsert_override
from src.generator.inflection.inflection_report import load_inflection_log, save_inflection_csv
from src.generator.generation.generator_agent import (
    _load_generator_state,
    _save_generator_state,
    clear_generator_stop_request,
    get_generator_status,
    prime_generator_state,
    request_generator_stop,
    run_generator_agent,
)
from src.generator.philologist.philologist_planner import build_philologist_plan
from src.jobs import create_job_id, resolve_job_paths
from src.jobs.storage import JOBS_DIR


def cleanup_batch_pdf_dir() -> None:
    if BATCH_PDF_DIR.exists():
        shutil.rmtree(BATCH_PDF_DIR)
    BATCH_PDF_DIR.mkdir(parents=True, exist_ok=True)


def process_web_row(payload: tuple[int, int, dict]) -> dict:
    result_index, outgoing_number, row = payload
    row_id = row.get("ID")
    mun_name = row.get("MUN_NAME", "unknown")
    started_at = perf_counter()
    print(f"[web-row:{result_index}] start id={row_id} mun={mun_name}")

    context_started_at = perf_counter()
    context = build_document_context(row, outgoing_number)
    print(
        f"[web-row:{result_index}] context_ready "
        f"id={row_id} elapsed={perf_counter() - context_started_at:.2f}s"
    )

    agent_started_at = perf_counter()
    agent_result = run_case_validation_agent(row, context)
    print(
        f"[web-row:{result_index}] case_agent_ready "
        f"id={row_id} status={agent_result.get('summary', {}).get('reviewed_fields_count', 0)}fields "
        f"elapsed={perf_counter() - agent_started_at:.2f}s"
    )

    apply_started_at = perf_counter()
    context = apply_case_agent_result(context, agent_result)
    print(
        f"[web-row:{result_index}] case_agent_applied "
        f"id={row_id} final_status={context.get('CASE_AGENT_STATUS')} "
        f"elapsed={perf_counter() - apply_started_at:.2f}s"
    )

    render_started_at = perf_counter()
    generated_files = generate_documents_for_row(row, context)
    print(
        f"[web-row:{result_index}] docx_ready "
        f"id={row_id} files={list(generated_files.keys())} "
        f"elapsed={perf_counter() - render_started_at:.2f}s total={perf_counter() - started_at:.2f}s"
    )
    return {
        "result_index": result_index,
        "id": row.get("ID"),
        "status": "ok",
        "case_agent_status": context.get("CASE_AGENT_STATUS"),
        "generated_files": generated_files,
    }


configure_documents_service(
    compact_generator_status=_compact_generator_status,
    get_generator_status=get_generator_status,
    compact_philologist_status=_compact_philologist_status,
    get_philologist_status=get_philologist_status,
    get_documents_thread=_get_documents_thread,
    get_generator_thread=_get_generator_thread,
    get_philologist_thread=_get_philologist_thread,
    save_generator_state=_save_generator_state,
    save_philologist_state=_save_philologist_state,
    chat_with_philologist=chat_with_philologist,
    run_generator_agent=run_generator_agent,
    clear_generator_stop_request=clear_generator_stop_request,
    run_philologist=run_philologist,
    clear_philologist_stop_request=clear_philologist_stop_request,
    load_generator_state=_load_generator_state,
    load_philologist_state=_load_philologist_state,
    schedule_output_archive_build=_schedule_output_archive_build,
    unregister_documents_thread=_unregister_documents_thread,
    build_job_readiness_result=_build_job_readiness_result,
    logger=logger,
)

configure_sender_service(
    run_sender=run_sender,
    logger=logger,
    load_sender_state=_load_sender_state,
    save_sender_state=_save_sender_state,
    unregister_sender_thread=_unregister_sender_thread,
    collect_excel_stats=_collect_excel_stats,
)


app.include_router(
    create_documents_router(
        check_auth=check_auth,
        prefer_existing_file=_prefer_existing_file,
        compact_documents_status=compact_documents_status,
        get_generator_thread=_get_generator_thread,
        get_philologist_thread=_get_philologist_thread,
        prime_philologist_running_state=_prime_philologist_running_state,
        start_documents_thread_if_absent=_start_documents_thread_if_absent,
        run_documents_pipeline_background=run_documents_pipeline_background,
        documents_job_key=_documents_job_key,
        clear_philologist_stop_request=clear_philologist_stop_request,
        get_generator_status=get_generator_status,
        get_philologist_status=lambda job_id: get_philologist_status(job_id, include_details=False),
        clear_generator_stop_request=clear_generator_stop_request,
        save_generator_state=_save_generator_state,
        prime_generator_state=prime_generator_state,
        request_generator_stop=request_generator_stop,
        request_philologist_stop=request_philologist_stop,
        documents_agent_choose_reply=documents_agent_choose_reply,
    )
)

app.include_router(
    create_sender_router(
        check_auth=check_auth,
        parse_optional_limit=_parse_optional_limit,
        compact_sender_status=compact_sender_status,
        clear_sender_stop_request=clear_sender_stop_request,
        prime_sender_checking_state=prime_sender_checking_state,
        prime_sender_running_state=prime_sender_running_state,
        start_sender_thread_if_absent=_start_sender_thread_if_absent,
        run_sender_background=run_sender_background,
        sender_job_key=_sender_job_key,
        get_sender_status=get_sender_status,
        get_unisender_history=get_unisender_history,
        build_unisender_delivery_analytics=build_unisender_delivery_analytics,
        settings=settings,
        append_unisender_go_events=append_unisender_go_events,
        logger=logger,
        request_sender_stop=request_sender_stop,
        preview_recipients=preview_recipients,
        chat_with_sender=chat_with_sender,
    )
)


@app.get("/api/counts")
async def counts(job_id: str | None = None, username: str = Depends(check_auth)):
    readiness = await job_readiness(job_id=job_id, username=username)
    counts_result = ((readiness or {}).get("result") or {}).get("counts") or {}
    return {
        "parser_total": int(counts_result.get("parser_total", 0) or 0),
        "generator_total": int(counts_result.get("generator_total", 0) or 0),
        "sender_total": int(counts_result.get("sender_total", 0) or 0),
    }


@app.post("/api/generate")
async def generate(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = str((payload or {}).get("job_id") or "").strip() or None
    xlsx_path = _prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
    if not xlsx_path.exists():
        raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")
    existing_thread = _get_generator_thread(job_id)
    if existing_thread is not None:
        return {"status": "ok", "result": _compact_generator_status(get_generator_status(job_id))}

    existing_state = get_generator_status(job_id)
    if str(existing_state.get("status") or "") == "running":
        return {"status": "ok", "result": _compact_generator_status(existing_state)}
    if str(existing_state.get("status") or "") == "stopped":
        clear_generator_stop_request(job_id)
        primed_state = existing_state
        primed_state["status"] = "running"
        primed_state["completed_at"] = None
        primed_state["summary_text"] = "Продолжаю генерацию с сохраненного места."
    else:
        primed_state = prime_generator_state(xlsx_path=xlsx_path, job_id=job_id)
    if primed_state.get("status") == "error":
        raise HTTPException(status_code=400, detail=primed_state.get("summary_text") or "Ошибка генерации")

    if primed_state.get("status") == "completed":
        _schedule_output_archive_build(job_id)
        return {"status": "ok", "result": _compact_generator_status(primed_state)}

    thread = threading.Thread(
        target=_run_generator_background,
        kwargs={"xlsx_path": xlsx_path, "job_id": job_id},
        daemon=True,
        name=f"generator-{_generator_job_key(job_id)}",
    )
    _register_generator_thread(job_id, thread)
    thread.start()
    return {"status": "ok", "result": _compact_generator_status(primed_state)}


@app.get("/api/generator/status")
async def generator_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": _compact_generator_status(get_generator_status(job_id))}


@app.post("/api/generator/stop")
async def generator_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    return {"status": "ok", "result": _compact_generator_status(request_generator_stop(job_id))}


from fastapi.responses import FileResponse
import zipfile

PUBLIC_ASSETS_DIR = Path("src/generator/assets")


@app.get("/public/mail-signature.png")
async def public_mail_signature():
    signature_path = PUBLIC_ASSETS_DIR / "parresh-signature-logo.png"
    if not signature_path.exists():
        raise HTTPException(status_code=404, detail="Mail signature image not found.")
    return FileResponse(signature_path, media_type="image/png")


@app.get("/public/onlyoffice/{token}/{filename}")
async def public_onlyoffice_document(token: str, filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise HTTPException(status_code=404, detail="Document not found.")
    safe_filename = Path(filename).name
    document_path = (ONLYOFFICE_PUBLIC_FILES_DIR / token / safe_filename).resolve()
    public_root = ONLYOFFICE_PUBLIC_FILES_DIR.resolve()
    if public_root not in document_path.parents or not document_path.exists():
        raise HTTPException(status_code=404, detail="Document not found.")
    return FileResponse(
        document_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=safe_filename,
    )


@app.get("/api/download/output")
async def download_output(job_id: str | None = None, username: str = Depends(check_auth)):
    output_dir = resolve_job_paths(job_id).output_dir
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")

    archive_path, cache_is_fresh = _resolve_cached_output_archive(job_id)
    if not cache_is_fresh:
        if not any(output_dir.rglob("*.*")):
            raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")
        archive_path = _build_output_archive(job_id)

    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename="output.zip"
    )


@app.get("/api/download/data-xlsx")
async def download_data_xlsx(job_id: str | None = None, username: str = Depends(check_auth)):
    data_path = _prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="Файл data.xlsx не найден.")
    return FileResponse(
        data_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="data.xlsx",
    )
@app.get("/api/parser/download-result")
async def download_parser_result(job_id: str | None = None, username: str = Depends(check_auth)):
    """Скачать последний обработанный файл задачи."""
    # 1. Приоритет — персональная папка задачи (изоляция по job_id)
    if job_id:
        try:
            paths = resolve_job_paths(job_id)
            latest = _latest_matching_file(
                [paths.output_dir], pattern="batch_*.xlsx", exclude_substring="FAILED"
            )
            if latest is not None:
                return FileResponse(
                    latest,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename=latest.name,
                )
        except Exception:
            pass

    # 2. Фолбэк — общий output/latest (старое поведение, если папки задачи нет)
    parser_output = Path(__file__).parent / "src" / "parser_new" / "output" / "latest"
    latest = _latest_matching_file(
        [parser_output], pattern="batch_*.xlsx", exclude_substring="FAILED"
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="Файл результата не найден")

    return FileResponse(
        latest,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=latest.name,
    )


@app.get("/api/parser/download-failed")
async def download_parser_failed(job_id: str | None = None, username: str = Depends(check_auth)):
    """Скачать файл с непроверенными МО."""
    search_dirs: list[Path] = []

    try:
        paths = resolve_job_paths(job_id)
        if paths.output_dir.exists():
            search_dirs.append(paths.output_dir)
    except Exception:
        pass

    parser_output = Path(__file__).parent / "src" / "parser_new" / "output" / "latest"
    if parser_output.exists():
        search_dirs.append(parser_output)

    latest = _latest_matching_file(search_dirs, pattern="*FAILED*.xlsx")
    if latest is None:
        raise HTTPException(status_code=404, detail="Файл непроверенных не найден")

    return FileResponse(
        latest,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=latest.name,
    )

@app.get("/api/download/sent-mail-log")
async def download_sent_mail_log(job_id: str | None = None, username: str = Depends(check_auth)):
    job_paths = resolve_job_paths(job_id)
    log_path = (
        job_paths.sent_mail_log_path
        if not job_paths.uses_legacy_layout
        else Path("data/sent_mail_log.jsonl")
    )
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Журнал отправленных писем пока не создан.")
    return FileResponse(
        log_path,
        media_type="application/x-ndjson",
        filename="sent_mail_log.jsonl",
    )


@app.get("/api/download/sender-delivery-report")
async def download_sender_delivery_report(job_id: str | None = None, username: str = Depends(check_auth)):
    if not unisender_delivery_report_has_data(job_id):
        raise HTTPException(status_code=404, detail="Журнал отправки UniSender пока пуст. Сначала запустите отправщик через UniSender.")
    job_paths = resolve_job_paths(job_id)
    report_path = _job_state_dir(job_id) / "unisender_delivery_report.xlsx"
    sent_log_path = (
        job_paths.sent_mail_log_path
        if not job_paths.uses_legacy_layout
        else Path("data/sent_mail_log.jsonl")
    )
    if not _is_cache_fresh(report_path, [sent_log_path], max_age_seconds=180):
        report_path = build_unisender_delivery_report_xlsx(job_id, refresh=True)
    return FileResponse(
        report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="unisender_delivery_report.xlsx",
    )


@app.get("/api/download/inflection-log")
async def download_inflection_log(job_id: str | None = None, username: str = Depends(check_auth)):
    job_paths = resolve_job_paths(job_id)
    log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Журнал склонений пока не создан.")
    return FileResponse(
        log_path,
        media_type="application/x-ndjson",
        filename="inflection_log.jsonl",
    )


@app.get("/api/download/inflection-report")
async def download_inflection_report(job_id: str | None = None, username: str = Depends(check_auth)):
    rows = load_inflection_log(job_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Журнал склонений пока не создан.")
    job_paths = resolve_job_paths(job_id)
    report_path = job_paths.root_dir / "state" / "inflection_report.csv"
    log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
    if not _is_cache_fresh(report_path, [log_path]):
        save_inflection_csv(rows, report_path)
    return FileResponse(
        report_path,
        media_type="text/csv",
        filename="inflection_report.csv",
    )


@app.get("/api/download/agent-memory")
async def download_agent_memory(job_id: str | None = None, username: str = Depends(check_auth)):
    candidates = build_learning_candidates(job_id)
    if not candidates:
        raise HTTPException(status_code=404, detail="Кандидаты для памяти агента пока не найдены.")
    report_path = get_agent_memory_csv_path(job_id)
    save_learning_memory_csv(candidates, report_path)
    return FileResponse(
        report_path,
        media_type="text/csv",
        filename="agent_memory_candidates.csv",
    )


@app.get("/api/download/agent-quarantine")
async def download_agent_quarantine(job_id: str | None = None, username: str = Depends(check_auth)):
    items = build_quarantine_items(job_id)
    if not items:
        raise HTTPException(status_code=404, detail="Карантин агента пока пуст.")
    report_path = get_agent_quarantine_csv_path(job_id)
    save_quarantine_csv(items, report_path)
    return FileResponse(
        report_path,
        media_type="text/csv",
        filename="agent_quarantine.csv",
    )


@app.get("/api/download/agent-report")
async def download_agent_report(job_id: str | None = None, username: str = Depends(check_auth)):
    report_text = build_agent_report(job_id)
    if not report_text.strip():
        raise HTTPException(status_code=404, detail="Отчет агента пока пуст.")
    report_path = get_agent_report_path(job_id)
    save_agent_report(job_id)
    return FileResponse(
        report_path,
        media_type="text/plain; charset=utf-8",
        filename="agent_report.txt",
    )


@app.get("/api/download/correction-report")
async def download_correction_report(job_id: str | None = None, username: str = Depends(check_auth)):
    if not correction_report_has_data(job_id):
        raise HTTPException(status_code=404, detail="Журнал исправлений пока пуст. Сначала запустите генератор/филолога.")
    report_path = _job_state_dir(job_id) / "journal_corrections_report.xlsx"
    source_paths = [
        _job_state_dir(job_id) / "philologist.json",
        _job_state_dir(job_id) / "inflection_log.jsonl",
    ]
    if not _is_cache_fresh(report_path, source_paths):
        report_path = build_correction_report_xlsx(job_id)
    return FileResponse(
        report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="journal_corrections_report.xlsx",
    )


@app.post("/api/agent-memory/approve-inflection")
async def approve_inflection_memory(payload: dict = Body(...), username: str = Depends(check_auth)):
    try:
        result = upsert_override(
            entity_type=str(payload.get("entity_type") or ""),
            source_value=str(payload.get("source_value") or ""),
            target_case=str(payload.get("target_case") or ""),
            result_value=str(payload.get("result_value") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "result": result}


@app.post("/api/philologist/run")
async def philologist_run(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    ai_enabled = True if payload is None else bool(payload.get("ai_enabled", True))
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    mode = None if payload is None else str(payload.get("mode") or "").strip().lower() or None
    existing_thread = _get_philologist_thread(job_id)
    if existing_thread:
        return {"status": "ok", "result": _compact_philologist_status(get_philologist_status(job_id, include_details=False))}

    existing_state = get_philologist_status(job_id, include_details=False)
    if str(existing_state.get("status") or "") in {"running", "finalizing"}:
        return {"status": "ok", "result": _compact_philologist_status(existing_state)}

    clear_philologist_stop_request(job_id)
    primed_state = _prime_philologist_running_state(job_id, mode or "fast")
    philologist_thread = threading.Thread(
        target=_run_philologist_background,
        kwargs={"ai_enabled": ai_enabled, "job_id": job_id, "mode": mode},
        daemon=True,
        name=f"philologist-{_philologist_job_key(job_id)}",
    )
    _register_philologist_thread(job_id, philologist_thread)
    philologist_thread.start()
    return {"status": "ok", "result": primed_state}


@app.get("/api/philologist/status")
async def philologist_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": _compact_philologist_status(get_philologist_status(job_id, include_details=False))}


@app.post("/api/philologist/stop")
async def philologist_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    return {"status": "ok", "result": _compact_philologist_status(request_philologist_stop(job_id))}


@app.get("/api/philologist/plan")
async def philologist_plan(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": build_philologist_plan(job_id)}


@app.post("/api/philologist/chat")
async def philologist_chat(
    payload: dict = Body(...),
    username: str = Depends(check_auth),
):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    job_id = str(payload.get("job_id") or "").strip() or None
    return {"status": "ok", **chat_with_philologist(message, job_id=job_id)}

from src.parser.agent import chat, clear_memory, get_memory, run_batch_parser, set_system_prompt

@app.post("/api/parser/chat-v2")
async def parser_chat_v2(payload: dict = Body(...), username: str = Depends(check_auth)):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    job_id = str(payload.get("job_id") or "").strip() or None
    result = chat(message, job_id=job_id)
    return result

@app.post("/api/parser/start")
async def parser_start(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
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

@app.get("/api/parser/memory")
async def parser_memory(job_id: str | None = None, username: str = Depends(check_auth)):
    return get_memory(job_id=job_id)

@app.post("/api/parser/memory/clear")
async def parser_memory_clear(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    clear_memory(job_id=job_id)
    return {"status": "ok"}

@app.post("/api/parser/prompt")
async def parser_prompt(payload: dict = Body(...), username: str = Depends(check_auth)):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Пустой промпт")
    job_id = str(payload.get("job_id") or "").strip() or None
    set_system_prompt(prompt, job_id=job_id)
    return {"status": "ok"}


@app.post("/api/parser/run")
async def parser_run(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    limit = _parse_optional_limit(payload)
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    result = run_parser_agent(limit=limit, job_id=job_id)
    return {"status": "ok", "result": result}


@app.get("/api/parser/status")
async def parser_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_parser_status(job_id)}

@app.post("/api/parser/merge-rmz")
async def merge_rmz(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    from src.parser.rmz_merger import run_merge
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    result = run_merge(job_id=job_id)
    # Если есть спорные совпадения — просим агента проверить
    if result.suspicious:
        suspicious_list = [
            {
                "mo_name": s.mo_name,
                "org_name": s.org_name,
                "sub_rf": s.sub_rf,
                "mun_r_name": s.mun_r_name,
                "reason": s.reason,
            }
            for s in result.suspicious
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


@app.post("/api/parser/chat")
async def parser_chat(
    payload: dict = Body(...),
    username: str = Depends(check_auth),
):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    job_id = str(payload.get("job_id") or "").strip() or None

    result = await run_in_threadpool(chat, message, job_id=job_id)

    # Если агент собрал новый файл — проверяем имена МО ИМЕННО на этом файле
    result_file = result.get("result_file")
    if result_file:
        try:
            from pathlib import Path as _Path
            from src.generator.verification.municipality_name_verifier import (
                verify_municipality_names_in_workbook,
            )
            verification = verify_municipality_names_in_workbook(_Path(result_file))
            result["municipality_name_verification"] = verification
            summary = format_municipality_verification_for_chat(verification, max_samples=20)
            if summary:
                logger.info(f"[parser] Верификация имён МО: {summary}")
        except Exception as e:
            logger.warning(f"Верификация имён МО не выполнена: {e}")

    return {"status": "ok", **result}

@app.get("/api/parser/progress")
async def parser_progress(job_id: str | None = None, username: str = Depends(check_auth)):
    job_key = str(job_id or "").strip()
    if not job_key:
        raise HTTPException(status_code=400, detail="Не указан job_id для потока прогресса")
    return StreamingResponse(
        parser_progress_subscribe(job_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # на случай, если впереди появится nginx
        },
    )

@app.get("/api/orchestrator/status")
async def orchestrator_status(session_id: str | None = None, username: str = Depends(check_auth)):
    raise HTTPException(status_code=404, detail="Оркестратор отключён в этой ветке.")


@app.get("/api/autonomous-worker/status")
async def autonomous_worker_status(username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_autonomous_worker_state()}


@app.post("/api/orchestrator/chat")
async def orchestrator_chat(
    payload: dict = Body(...),
    username: str = Depends(check_auth),
):
    raise HTTPException(status_code=404, detail="Оркестратор отключён в этой ветке.")


if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск сервера", host=settings.app_host, port=settings.app_port)
    uvicorn.run("main:app", host=settings.app_host, port=settings.app_port)
