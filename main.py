from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets
import re
from src.utils.logger import logger
from src.utils.config import settings
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Body, Form
import shutil
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from time import perf_counter
import time

app = FastAPI(title="Mailing Agent")
security = HTTPBasic()
_sender_threads: dict[str, threading.Thread] = {}
_sender_threads_lock = threading.Lock()
_philologist_threads: dict[str, threading.Thread] = {}
_philologist_threads_lock = threading.Lock()
_generator_threads: dict[str, threading.Thread] = {}
_generator_threads_lock = threading.Lock()
_parser_verification_threads: dict[str, threading.Thread] = {}
_parser_verification_threads_lock = threading.Lock()


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


def _sender_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _philologist_job_key(job_id: str | None) -> str:
    return str(job_id or "__legacy__")


def _generator_job_key(job_id: str | None) -> str:
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


def _get_parser_verification_thread(job_id: str | None) -> threading.Thread | None:
    key = _parser_job_key(job_id)
    with _parser_verification_threads_lock:
        thread = _parser_verification_threads.get(key)
        if thread and not thread.is_alive():
            _parser_verification_threads.pop(key, None)
            return None
        return thread


def _register_sender_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _sender_threads_lock:
        _sender_threads[_sender_job_key(job_id)] = thread


def _register_philologist_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _philologist_threads_lock:
        _philologist_threads[_philologist_job_key(job_id)] = thread


def _register_generator_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _generator_threads_lock:
        _generator_threads[_generator_job_key(job_id)] = thread


def _register_parser_verification_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _parser_verification_threads_lock:
        _parser_verification_threads[_parser_job_key(job_id)] = thread


def _compact_philologist_status(state: dict) -> dict:
    documents = state.get("documents") or []
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
        "document_count": len(documents),
        "tool_trace_count": len(state.get("tool_trace") or []),
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


def _run_sender_background(*, limit: int | None, transport: str | None, job_id: str | None) -> None:
    try:
        run_sender(dry_run=False, limit=limit, transport=transport, auto_recover=False, job_id=job_id)
    except Exception:
        logger.exception("sender_background_failed", job_id=job_id, transport=transport)
    finally:
        with _sender_threads_lock:
            _sender_threads.pop(_sender_job_key(job_id), None)


def _run_philologist_background(*, ai_enabled: bool, job_id: str | None, mode: str | None) -> None:
    try:
        run_philologist(ai_enabled=ai_enabled, job_id=job_id, mode=mode)
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
        run_generator_agent(xlsx_path=xlsx_path, job_id=job_id)
    except Exception:
        logger.exception("generator_background_failed", job_id=job_id)
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
            _parser_verification_threads.pop(_parser_job_key(job_id), None)


def _prime_sender_running_state(job_id: str | None, transport: str | None) -> dict:
    state = _load_sender_state(job_id)
    stats = _collect_excel_stats(resolve_job_paths(job_id).data_xlsx)
    total_rows = int(state.get("total_rows") or stats.get("total", 0) or 0)
    state["status"] = "running"
    state["mode"] = "send"
    state["transport"] = transport or state.get("transport") or "smtp"
    state["started_at"] = datetime.now().isoformat(timespec="seconds")
    state["completed_at"] = None
    state["processed_rows"] = 0
    state["ready_rows"] = 0
    state["sent_rows"] = int(stats.get("sent", 0))
    state["error_rows"] = 0
    state["skipped_rows"] = 0
    state["warning_rows"] = 0
    state["handoff_rows"] = 0
    state["generator_handoff_rows"] = 0
    state["philology_blocked_rows"] = 0
    state["autonomous_recovery_rows"] = 0
    state["rows"] = []
    state["stats"] = stats
    state["total_rows"] = total_rows
    state["remaining_rows"] = total_rows
    state["summary_text"] = "Агент-отправщик начал отправку писем."
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _save_sender_state(state, job_id)
    return state


def _prime_philologist_running_state(job_id: str | None, mode: str | None) -> dict:
    paths = resolve_job_paths(job_id)
    output_dir = paths.output_dir
    docx_count = len(list(output_dir.rglob("*.docx"))) if output_dir.exists() else 0
    state = _load_philologist_state(job_id)
    if str(state.get("status") or "") == "stopped":
        state["status"] = "running"
        state["completed_at"] = None
        state["mode"] = mode or state.get("mode") or "fast"
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
    file: UploadFile = File(...),
    job_id: str | None = Form(default=None),
    username: str = Depends(check_auth),
):
    request_started = perf_counter()
    logger.info("upload_data_request_started", filename=file.filename, requested_job_id=job_id)
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
        filename=file.filename,
        job_id=paths.job_id,
        file_save_seconds=file_save_seconds,
        request_seconds=round(perf_counter() - request_started, 3),
    )

    existing_thread = _get_parser_verification_thread(paths.job_id)
    if existing_thread is None:
        verification_thread = threading.Thread(
            target=_run_parser_verification_background,
            kwargs={"job_id": paths.job_id, "source": "upload"},
            daemon=True,
            name=f"parser-verify-{_parser_job_key(paths.job_id)}",
        )
        _register_parser_verification_thread(paths.job_id, verification_thread)
        verification_thread.start()
        logger.info("upload_data_verification_scheduled", filename=file.filename, job_id=paths.job_id)
    else:
        logger.info("upload_data_verification_already_running", filename=file.filename, job_id=paths.job_id)

    return {
        "status": "ok",
        "filename": file.filename,
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
    _, _, rows = load_rows(data_path)
    return {"loaded": True, "total": len(rows)}


@app.get("/api/job/readiness")
async def job_readiness(job_id: str | None = None, username: str = Depends(check_auth)):
    paths = resolve_job_paths(job_id)
    data_path = _prefer_existing_file(paths.data_xlsx, Path("data/data.xlsx"))
    row_count = 0
    if data_path.exists():
        try:
            _, _, rows = load_rows(data_path)
            row_count = len(rows)
        except Exception:
            row_count = 0

    templates_dir = paths.templates_dir
    kp_template_loaded = (templates_dir / "kp_template_source.docx").exists()
    contract_template_loaded = (templates_dir / "contract_template_source.docx").exists()
    mail_template_loaded = any(
        (templates_dir / name).exists()
        for name in ("mail_template.docx", "mail_template.txt")
    )

    output_dir = paths.output_dir
    output_docx_count = (
        sum(1 for path in output_dir.rglob("*.docx") if path.is_file())
        if output_dir.exists()
        else 0
    )
    output_pdf_count = (
        sum(1 for path in output_dir.rglob("*.pdf") if path.is_file())
        if output_dir.exists()
        else 0
    )

    parser_state = get_parser_status(job_id)
    generator_state = get_generator_status(job_id)
    philologist_state = get_philologist_status(job_id)

    parser_running = str(parser_state.get("status") or "") == "running"
    generator_running = str(generator_state.get("status") or "") == "running"
    philologist_running = str(philologist_state.get("status") or "") == "running"

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

    if output_docx_count <= 0:
        philologist_reasons.append("Нет готовых DOCX-документов.")
    if generator_running:
        philologist_reasons.append("Генератор ещё работает.")

    if output_pdf_count <= 0:
        sender_reasons.append("Нет готовых PDF-вложений.")
    if generator_running:
        sender_reasons.append("Генератор ещё работает.")
    if philologist_running:
        sender_reasons.append("Филолог ещё работает.")

    return {
        "status": "ok",
        "result": {
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
        },
    }


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
    original_name = Path(file.filename or "").name
    kind = (template_kind or "").strip().lower()
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
from src.generator.generation.pdf_converter import convert_docx_batch
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
    build_unisender_delivery_report_xlsx,
    unisender_delivery_report_has_data,
)
from src.generator.orchestration.parser_agent import (
    chat_with_parser,
    format_municipality_verification_for_chat,
    get_parser_status,
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
    clear_generator_stop_request,
    get_generator_status,
    prime_generator_state,
    request_generator_stop,
    run_generator_agent,
)
from src.generator.philologist.philologist_planner import build_philologist_plan
from src.jobs import create_job_id, resolve_job_paths


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


def finalize_generated_files(results: list[dict]) -> None:
    jobs = build_docx_jobs(results)
    logger.info("web_finalize_start", staged_docx_count=len(jobs))
    staged_docx_paths = [job["staged_docx"] for job in jobs]
    started_at = perf_counter()
    pdf_map = convert_docx_batch(staged_docx_paths, BATCH_PDF_DIR, chunk_size=100, worker_count=1)
    logger.info(
        "web_pdf_batch_done",
        staged_docx_count=len(staged_docx_paths),
        converted_count=sum(1 for value in pdf_map.values() if value),
        elapsed_seconds=round(perf_counter() - started_at, 2),
    )

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


@app.get("/api/counts")
async def counts(job_id: str | None = None, username: str = Depends(check_auth)):
    paths = resolve_job_paths(job_id)
    if job_id:
        base_path = paths.base_xlsx
        data_path = paths.data_xlsx
    else:
        base_path = _prefer_existing_file(paths.base_xlsx, Path("service_docs/base.xlsx"))
        data_path = _prefer_existing_file(paths.data_xlsx, Path("data/data.xlsx"))
    
    parser_total = 0
    if base_path.exists():
        _, _, rows = load_rows(base_path)
        parser_total = len(rows)
    
    generator_total = 0
    if data_path.exists():
        _, _, rows = load_rows(data_path)
        generator_total = len(rows)

    # After restarts the UI can recover module states earlier than it can
    # reliably re-read every source file. Use persisted agent state as a
    # fallback so the counters stay in sync with the real job progress.
    generator_state = get_generator_status(job_id)
    philologist_state = get_philologist_status(job_id)
    sender_state = get_sender_status(job_id)

    generator_total = max(
        generator_total,
        int(generator_state.get("total_rows", 0) or 0),
    )
    if generator_total <= 0:
        philologist_total = int(philologist_state.get("total_documents", 0) or 0)
        if philologist_total > 0:
            # Philologist works on both KP and contract DOCX/PDF files.
            generator_total = max(generator_total, philologist_total // 2)

    sender_total = max(
        generator_total,
        int(sender_state.get("total_rows", 0) or 0),
        int((sender_state.get("stats") or {}).get("total", 0) or 0),
    )
    
    return {
        "parser_total": parser_total,
        "generator_total": generator_total,
        "sender_total": sender_total
    }

@app.post("/api/generate")
async def generate(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = str((payload or {}).get("job_id") or "").strip() or None
    xlsx_path = _prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
    if not xlsx_path.exists():
        raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")
    existing_thread = _get_generator_thread(job_id)
    if existing_thread is not None:
        return {"status": "ok", "result": get_generator_status(job_id)}

    existing_state = get_generator_status(job_id)
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
        return {"status": "ok", "result": primed_state}

    thread = threading.Thread(
        target=_run_generator_background,
        kwargs={"xlsx_path": xlsx_path, "job_id": job_id},
        daemon=True,
        name=f"generator-{_generator_job_key(job_id)}",
    )
    _register_generator_thread(job_id, thread)
    thread.start()
    return {"status": "ok", "result": primed_state}


@app.get("/api/generator/status")
async def generator_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_generator_status(job_id)}


@app.post("/api/generator/stop")
async def generator_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    return {"status": "ok", "result": request_generator_stop(job_id)}


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
    if not output_dir.exists() or not list(output_dir.rglob("*.*")):
        raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")

    archive_path, cache_is_fresh = _resolve_cached_output_archive(job_id)
    if not cache_is_fresh:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in output_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(output_dir))

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
    """Скачать последний обработанный файл."""
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

    latest = _latest_matching_file(search_dirs, pattern="*.xlsx", exclude_substring="FAILED")
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
        return {"status": "ok", "result": _compact_philologist_status(get_philologist_status(job_id))}

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
    return {"status": "ok", "result": _compact_philologist_status(get_philologist_status(job_id))}


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


@app.post("/api/sender/run")
async def sender_run(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    dry_run = True if payload is None else bool(payload.get("dry_run", True))
    limit = _parse_optional_limit(payload)
    transport = None if payload is None else payload.get("transport")
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    if dry_run:
        result = run_sender(dry_run=True, limit=limit, transport=transport, auto_recover=False, job_id=job_id)
        return {"status": "ok", "result": result}

    existing_thread = _get_sender_thread(job_id)
    if existing_thread:
        return {"status": "ok", "result": get_sender_status(job_id)}

    clear_sender_stop_request(job_id)
    primed_state = _prime_sender_running_state(job_id, transport)
    sender_thread = threading.Thread(
        target=_run_sender_background,
        kwargs={"limit": limit, "transport": transport, "job_id": job_id},
        daemon=True,
        name=f"sender-{_sender_job_key(job_id)}",
    )
    _register_sender_thread(job_id, sender_thread)
    sender_thread.start()
    return {"status": "ok", "result": primed_state}


@app.get("/api/sender/status")
async def sender_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_sender_status(job_id)}


@app.get("/api/sender/unisender-history")
async def sender_unisender_history(
    job_id: str | None = None,
    limit: int = 50,
    refresh: bool = False,
    username: str = Depends(check_auth),
):
    return {
        "status": "ok",
        "result": get_unisender_history(job_id=job_id, limit=limit, refresh=refresh),
    }


@app.post("/api/sender/stop")
async def sender_stop(payload: dict | None = Body(default=None), username: str = Depends(check_auth)):
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    result = request_sender_stop(job_id=job_id)
    return {"status": "ok", "result": result}


@app.post("/api/sender/preview")
async def sender_preview(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    limit = _parse_optional_limit(payload)
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    result = preview_recipients(limit=limit, job_id=job_id)
    return {"status": "ok", "result": result}


@app.post("/api/sender/chat")
async def sender_chat(
    payload: dict = Body(...),
    username: str = Depends(check_auth),
):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    job_id = str(payload.get("job_id") or "").strip() or None
    return {"status": "ok", **chat_with_sender(message, job_id=job_id)}


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
    return {"status": "ok", **chat(message, job_id=job_id)}


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
    uvicorn.run("main:app", host=settings.app_host, port=settings.app_port, reload=True)
