from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import zipfile
from src.security.auth import principal_from_user_record
from src.security.auth_bootstrap import bootstrap_auth_store
from src.security.bearer_auth import resolve_request_username
from src.security.session_store import SESSION_COOKIE_NAME, get_session_username
from src.security.user_store import get_user_record
from src.utils.logger import logger
from src.utils.config import SecurityConfigurationError, require_configured_app_password, settings
from src.jobs.access import read_job_owner
from src.workers.process_manager import (
    _count_user_active_workers,
    list_worker_statuses,
    start_worker_process_thread,
    terminate_worker_process,
)
from src.web.agent_router import create_agent_router
from src.web.consent_router import create_consent_router, recover_pending_materials_dispatches
from src.web.chain_router import create_chain_router
from src.web.documents_router import create_documents_router
from src.web.documents_service import (
    compact_documents_status,
    configure_documents_service,
    documents_agent_choose_reply,
    run_documents_pipeline_background,
)
from src.web.download_router import create_download_router
from src.web.preview_router import create_preview_router
from src.web.generator_router import create_generator_router
from src.web.jobs_router import JobsWebController
from src.web.load_test_service import create_documents_load_test_job, is_load_test_job
from src.web.upload_validation import validate_uploaded_file
from src.web.parser_router import create_parser_router
from src.web.philologist_router import create_philologist_router
from src.web.public_router import create_public_router
from src.web.sender_router import create_sender_router
from src.web.statistics_router import create_statistics_router
from src.web.smtp_router import create_smtp_router
from src.web.auth_router import create_auth_router
from src.web.companies_router import create_companies_router
from src.web.v1_router import create_v1_router
from src.web.workers_router import create_workers_router
from src.web.sender_service import (
    compact_sender_status,
    configure_sender_service,
    prime_sender_checking_state,
    prime_sender_running_state,
    prime_sender_queued_state,
    prime_sender_scheduled_state,
    run_sender_background,
)
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
import shutil
import threading
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from time import perf_counter
import time
from src.parser_new.progress import subscribe as parser_progress_subscribe

app = FastAPI(title="Mailing Agent")
PROJECT_ROOT = Path(__file__).resolve().parent
_sender_threads: dict[str, threading.Thread] = {}
_sender_threads_lock = threading.Lock()
_philologist_threads: dict[str, threading.Thread] = {}
_philologist_threads_lock = threading.Lock()
_generator_threads: dict[str, threading.Thread] = {}
_generator_threads_lock = threading.Lock()
_documents_threads: dict[str, threading.Thread] = {}
_documents_threads_lock = threading.Lock()
_parser_threads: dict[str, threading.Thread] = {}
_parser_threads_lock = threading.Lock()
_parser_verification_threads: dict[str, threading.Thread] = {}
_parser_verification_threads_lock = threading.Lock()
_output_archive_threads: dict[str, threading.Thread] = {}
_output_archive_threads_lock = threading.Lock()
_consent_materials_recovery_thread: threading.Thread | None = None
_consent_materials_recovery_thread_lock = threading.Lock()
_consent_materials_recovery_stop_event = threading.Event()


def _start_stats_cache_warm_thread() -> None:
    if not bool(settings.stats_cache_warm_enabled):
        return
    from src.generator.delivery.manager_stats import start_stats_cache_warm_loop

    interval_seconds = max(60, int(settings.stats_cache_warm_interval_seconds or 1200))
    start_stats_cache_warm_loop(interval_seconds=interval_seconds)


@app.on_event("startup")
async def app_startup():
    require_configured_app_password(settings)
    from src.infra.db import init_db
    from src.infra.object_store import ensure_bucket

    await run_in_threadpool(init_db)
    await run_in_threadpool(ensure_bucket)
    await run_in_threadpool(bootstrap_auth_store, settings)
    # Demo seed is intentionally NOT run during startup — it can exceed healthcheck
    # start_period. Use `scripts/dev.ps1 start|seed` or SEED after the app is healthy.
    if bool(getattr(settings, "seed_demo_data_on_startup", False)):
        import threading

        def _seed_bg() -> None:
            try:
                from src.campaigns.seed import seed_demo_data

                seed_demo_data(force=False)
            except Exception:
                logger.exception("seed_demo_data_on_startup_failed")

        threading.Thread(target=_seed_bg, name="seed-demo-data", daemon=True).start()
    _start_consent_materials_recovery_thread()
    _start_stats_cache_warm_thread()
    from src.workers.queue_worker import start_queue_worker

    start_queue_worker(project_root=PROJECT_ROOT)
    return None


@app.on_event("shutdown")
async def app_shutdown():
    _consent_materials_recovery_stop_event.set()
    from src.generator.delivery.manager_stats import stop_stats_cache_warm_loop

    stop_stats_cache_warm_loop()
    from src.workers.queue_worker import stop_queue_worker

    stop_queue_worker()
    return None


def _start_consent_materials_recovery_thread() -> None:
    if bool(settings.background_queue_enabled):
        return
    if not bool(settings.consent_materials_recovery_enabled):
        return
    with _consent_materials_recovery_thread_lock:
        global _consent_materials_recovery_thread
        if _consent_materials_recovery_thread and _consent_materials_recovery_thread.is_alive():
            return
        _consent_materials_recovery_stop_event.clear()
        _consent_materials_recovery_thread = threading.Thread(
            target=_run_consent_materials_recovery_loop,
            daemon=True,
            name="consent-materials-recovery",
        )
        _consent_materials_recovery_thread.start()


def _run_consent_materials_recovery_loop() -> None:
    poll_seconds = max(10, int(settings.consent_materials_recovery_poll_seconds or 60))
    batch_size = max(1, int(settings.consent_materials_recovery_batch_size or 25))
    while not _consent_materials_recovery_stop_event.is_set():
        try:
            result = recover_pending_materials_dispatches(limit=batch_size)
            if any(int(result.get(key) or 0) for key in ("checked", "sent", "failed", "skipped")):
                logger.info("consent_materials_recovery_tick", **result)
        except Exception as exc:
            logger.exception("consent_materials_recovery_failed", error=str(exc))
        _consent_materials_recovery_stop_event.wait(poll_seconds)

def check_auth(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
):
    try:
        require_configured_app_password(settings)
    except SecurityConfigurationError as exc:
        logger.error("app_auth_not_configured", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис не настроен: APP_PASSWORD не задан.",
        ) from exc

    username = resolve_request_username(
        session_token=session_token,
        authorization=authorization,
        settings_obj=settings,
    )

    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход в систему",
        )
    record = get_user_record(username)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия недействительна",
        )
    return principal_from_user_record(record)


def _parse_optional_limit(payload: dict | None) -> int | None:
    if not payload:
        return None
    raw_value = payload.get("limit")
    if raw_value in (None, ""):
        return None
    text_value = str(raw_value).strip()
    if not text_value:
        return None
    try:
        return int(text_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Параметр limit должен быть целым числом.",
        ) from exc


def _prefer_existing_file(primary: Path, fallback: Path) -> Path:
    return primary if primary.exists() else fallback


def _validate_uploaded_file(
    upload: UploadFile,
    *,
    allowed_extensions: tuple[str, ...],
    max_bytes: int,
    human_name: str,
) -> str:
    return validate_uploaded_file(
        upload,
        allowed_extensions=allowed_extensions,
        max_bytes=max_bytes,
        human_name=human_name,
    )


_METADATA_CACHE_LOCK = threading.Lock()
_EXCEL_ROW_COUNT_CACHE: dict[str, dict[str, float | int | tuple[int, int]]] = {}
_TREE_FILE_COUNT_CACHE: dict[str, dict[str, float | int]] = {}
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


def _output_archive_path(job_id: str | None) -> Path:
    paths = resolve_job_paths(job_id)
    archive_path = paths.root_dir / "archives" / "output.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    return archive_path


def _archive_contains_output_files(archive_path: Path, output_dir: Path, output_files: list[Path]) -> bool:
    if not output_files:
        return False
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archived_names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    expected_names = {path.relative_to(output_dir).as_posix() for path in output_files}
    return expected_names.issubset(archived_names)


def _resolve_cached_output_archive(job_id: str | None) -> tuple[Path, bool]:
    job_paths = resolve_job_paths(job_id)
    output_dir = job_paths.output_dir
    archive_path = _output_archive_path(job_id)
    if not output_dir.exists():
        return archive_path, False
    output_files = _iter_output_archive_files(output_dir)
    if not output_files:
        return archive_path, False
    if not archive_path.exists():
        return archive_path, False
    try:
        archive_mtime = archive_path.stat().st_mtime
    except OSError:
        return archive_path, False
    cache_is_fresh = archive_mtime >= _latest_tree_mtime(output_dir)
    if cache_is_fresh:
        cache_is_fresh = _archive_contains_output_files(archive_path, output_dir, output_files)
    return archive_path, cache_is_fresh


def _iter_output_archive_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != OUTPUT_FOLDER_MANIFEST_FILENAME
    ]


def _build_output_archive(job_id: str | None) -> Path:
    _sync_job_pull(job_id, subdirs=["output", "archives"])
    output_dir = resolve_job_paths(job_id).output_dir
    output_files = _iter_output_archive_files(output_dir)
    if not output_files:
        raise FileNotFoundError("No generated output files found for archive")
    archive_path, _ = _resolve_cached_output_archive(job_id)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temp_archive_path = archive_path.with_suffix(".tmp.zip")
    if temp_archive_path.exists():
        try:
            temp_archive_path.unlink()
        except OSError:
            pass
    with zipfile.ZipFile(temp_archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_files:
            zf.write(f, f.relative_to(output_dir))
    temp_archive_path.replace(archive_path)
    if normalize_job_id(job_id):
        try:
            from src.jobs.workspace import push_job

            push_job(job_id, ["archives"])
        except ValueError:
            pass
    return archive_path


def _schedule_output_archive_build(job_id: str | None) -> None:
    if bool(settings.background_queue_enabled):
        from src.workers.task_queue import enqueue_task

        owner = read_job_owner(job_id)
        enqueue_task(
            task_type="output_archive",
            job_id=job_id,
            payload={"job_id": job_id},
            owner_username=str(owner.get("owner_username") or ""),
            max_attempts=max(1, int(settings.background_queue_max_attempts or 3)),
        )
        return


    key = _generator_job_key(job_id)
    with _output_archive_threads_lock:
        existing = _output_archive_threads.get(key)
        if existing and existing.is_alive():
            return

        def _run() -> None:
            try:
                if not bool(compact_documents_status(job_id).get("output_ready")):
                    logger.info("output_archive_build_waiting_for_ready_documents", job_id=job_id)
                    return
                output_dir = resolve_job_paths(job_id).output_dir
                if _iter_output_archive_files(output_dir):
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

def _get_parser_thread(job_id: str | None) -> threading.Thread | None:
    key = _parser_job_key(job_id)
    with _parser_threads_lock:
        thread = _parser_threads.get(key)
        if thread and not thread.is_alive():
            _parser_threads.pop(key, None)
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


def _ensure_generator_user_limit(job_id: str | None) -> None:
    _ensure_user_inprocess_limit(
        job_id,
        registry=_generator_threads,
        registry_lock=_generator_threads_lock,
    )


def _ensure_philologist_user_limit(job_id: str | None) -> None:
    _ensure_user_inprocess_limit(
        job_id,
        registry=_philologist_threads,
        registry_lock=_philologist_threads_lock,
    )


def _mark_worker_process_failed(task: str, job_id: str | None, message: str) -> None:
    completed_at = datetime.now().isoformat(timespec="seconds")
    if task == "sender":
        state = _load_sender_state(job_id)
        if str(state.get("status") or "") == "running":
            state["status"] = "error"
            state["completed_at"] = completed_at
            state["summary_text"] = f"Агент-отправщик остановился с ошибкой: {message}"
            _save_sender_state(state, job_id)
        return

    if task == "documents":
        generator_state = _load_generator_state(job_id)
        philologist_state = _load_philologist_state(job_id)
        if str(generator_state.get("status") or "") == "running":
            generator_state["status"] = "error"
            generator_state["completed_at"] = completed_at
            generator_state["summary_text"] = f"Подготовка документов остановилась с ошибкой: {message}"
            _save_generator_state(generator_state, job_id)
        elif str(philologist_state.get("status") or "") in {"running", "finalizing"}:
            philologist_state["status"] = "error"
            philologist_state["completed_at"] = completed_at
            philologist_state["summary_text"] = f"Проверка документов остановилась с ошибкой: {message}"
            _save_philologist_state(philologist_state, job_id)

    if task in {"parser_start", "parser_agent"}:
        state = _load_parser_state(job_id)
        state["status"] = "error"
        state["completed_at"] = completed_at
        state["summary_text"] = f"Парсер остановился с ошибкой: {message}"
        _save_parser_state(state, job_id)
        return

def _start_background_worker_process(
    job_id: str | None,
    *,
    task: str,
    kwargs: dict | None = None,
    name: str | None = None,
    registry: dict[str, threading.Thread],
    registry_lock: threading.Lock,
    key_factory,
    unregister,
    max_workers: int,
    timeout_seconds: int,
    before_start=None,
) -> tuple[threading.Thread, bool]:
    owner = read_job_owner(job_id)
    owner_username = str(owner.get("owner_username") or "")
    return start_worker_process_thread(
        job_id,
        task=task,
        kwargs=kwargs,
        name=name,
        registry=registry,
        registry_lock=registry_lock,
        key_factory=key_factory,
        unregister=unregister,
        state_dir_factory=_job_state_dir,
        project_root=PROJECT_ROOT,
        mark_failed=_mark_worker_process_failed,
        logger=logger,
        max_workers=max_workers,
        user_max_workers=max(1, int(settings.user_worker_max_processes_per_task or 1)),
        owner_username=owner_username,
        timeout_seconds=timeout_seconds,
        before_start=before_start,
    )


def _ensure_user_inprocess_limit(
    job_id: str | None,
    *,
    registry: dict[str, threading.Thread],
    registry_lock: threading.Lock,
) -> None:
    owner = read_job_owner(job_id)
    owner_username = str(owner.get("owner_username") or "")
    if not owner_username:
        return
    user_max = max(1, int(settings.user_inprocess_max_tasks or 1))
    with registry_lock:
        active_count = _count_user_active_workers(registry, owner_username)
    if active_count >= user_max:
        raise RuntimeError(
            f"Достигнут лимит фоновых задач для пользователя {owner_username}: {active_count}/{user_max}."
        )


def _start_sender_thread_if_absent(
    job_id: str | None,
    *,
    target,
    kwargs: dict | None = None,
    name: str | None = None,
    before_start=None,
    available_at: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    if before_start is not None:
        before_start()
    owner = read_job_owner(job_id)
    owner_username = str(owner.get("owner_username") or "")
    from src.workers.queue_worker import enqueue_sender_task

    queue_result = enqueue_sender_task(
        job_id=job_id,
        owner_username=owner_username,
        kwargs=dict(kwargs or {}),
        available_at=available_at,
    )
    return queue_result, bool(queue_result.get("created"))


def _start_documents_thread_if_absent(
    job_id: str | None,
    *,
    target,
    kwargs: dict | None = None,
    name: str | None = None,
) -> tuple[threading.Thread, bool]:
    return _start_background_worker_process(
        job_id,
        task="documents",
        kwargs=kwargs,
        name=name,
        registry=_documents_threads,
        registry_lock=_documents_threads_lock,
        key_factory=_documents_job_key,
        unregister=_unregister_documents_thread,
        max_workers=max(1, int(settings.documents_worker_max_processes or 1)),
        timeout_seconds=max(0, int(settings.documents_worker_timeout_seconds or 0)),
    )

def _prime_parser_running_state(job_id: str | None, task: str) -> dict:
    state = _load_parser_state(job_id)
    started_at = datetime.now().isoformat(timespec="seconds")
    state["status"] = "running"
    state["started_at"] = started_at
    state["completed_at"] = None
    state["summary_text"] = (
        "Парсер запущен в фоне. Ищу и проверяю данные."
        if task == "parser_start"
        else "Агент-парсер запущен в фоне и обрабатывает очередь задач."
    )
    _save_parser_state(state, job_id)
    return state


def _start_parser_thread_if_absent(
    job_id: str | None,
    *,
    task: str,
    kwargs: dict | None = None,
    name: str | None = None,
) -> tuple[threading.Thread, bool]:
    return _start_background_worker_process(
        job_id,
        task=task,
        kwargs=kwargs,
        name=name,
        registry=_parser_threads,
        registry_lock=_parser_threads_lock,
        key_factory=_parser_job_key,
        unregister=_unregister_parser_thread,
        max_workers=1,
        timeout_seconds=0,
        before_start=lambda: _prime_parser_running_state(job_id, task),
    )


def _unregister_generator_task(job_id: str | None) -> None:
    with _generator_threads_lock:
        _generator_threads.pop(_generator_job_key(job_id), None)


def _unregister_philologist_task(job_id: str | None) -> None:
    with _philologist_threads_lock:
        _philologist_threads.pop(_philologist_job_key(job_id), None)


def _start_generator_task(
    job_id: str | None,
    *,
    xlsx_path: Path,
    name: str | None = None,
) -> tuple[threading.Thread, bool]:
    return _start_background_worker_process(
        job_id,
        task="generator",
        kwargs={"xlsx_path": xlsx_path, "job_id": job_id},
        name=name,
        registry=_generator_threads,
        registry_lock=_generator_threads_lock,
        key_factory=_generator_job_key,
        unregister=_unregister_generator_task,
        max_workers=max(1, int(settings.documents_worker_max_processes or 1)),
        timeout_seconds=max(0, int(settings.documents_worker_timeout_seconds or 0)),
    )


def _start_philologist_task(
    job_id: str | None,
    *,
    ai_enabled: bool,
    mode: str | None,
    name: str | None = None,
) -> tuple[threading.Thread, bool]:
    return _start_background_worker_process(
        job_id,
        task="philologist",
        kwargs={"ai_enabled": ai_enabled, "mode": mode, "job_id": job_id},
        name=name,
        registry=_philologist_threads,
        registry_lock=_philologist_threads_lock,
        key_factory=_philologist_job_key,
        unregister=_unregister_philologist_task,
        max_workers=max(1, int(settings.documents_worker_max_processes or 1)),
        timeout_seconds=max(0, int(settings.documents_worker_timeout_seconds or 0)),
    )



def _unregister_sender_thread(job_id: str | None) -> None:
    with _sender_threads_lock:
        _sender_threads.pop(_sender_job_key(job_id), None)


def _unregister_documents_thread(job_id: str | None) -> None:
    with _documents_threads_lock:
        _documents_threads.pop(_documents_job_key(job_id), None)


def _unregister_parser_thread(job_id: str | None) -> None:
    with _parser_threads_lock:
        _parser_threads.pop(_parser_job_key(job_id), None)


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
        "document_mode": state.get("document_mode", ""),
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
    current_client = state.get("current_client")
    if isinstance(current_client, dict):
        current_client = {
            "index": current_client.get("index"),
            "total": current_client.get("total"),
            "row_id": current_client.get("row_id"),
            "name": current_client.get("name"),
        }
    generated_docx_count = int(state.get("staged_docx_count") or 0)
    if generated_docx_count <= 0:
        generated_docx_count = sum(
            1
            for result in (state.get("results") or [])
            if isinstance(result, dict)
            for key, value in (result.get("generated_files") or {}).items()
            if key in {"kp", "contract"} and str(value or "").lower().endswith(".docx")
        )
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
        "document_mode": state.get("document_mode", ""),
        "work_type": state.get("work_type", ""),
        "summary_text": state.get("summary_text", ""),
        "generated_docx_count": generated_docx_count,
        "staged_docx_count": state.get("staged_docx_count", 0),
        "staged_pdf_count": state.get("staged_pdf_count", 0),
        "pdf_total": state.get("pdf_total", 0),
        "pdf_processed": state.get("pdf_processed", 0),
        "output_file_count": state.get("output_file_count", 0),
        "inflection_summary": inflection_summary if isinstance(inflection_summary, dict) else {},
        "template_review": state.get("template_review", {}),
        "current_client": current_client,
        "stop_requested": state.get("stop_requested", False),
        "task_stats": state.get("task_stats", {}),
        "recent_events": (state.get("recent_events") or [])[:5],
    }


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _sync_job_pull(job_id: str | None, *, subdirs: list[str] | None = None) -> None:
    if not normalize_job_id(job_id):
        return
    try:
        from src.jobs.workspace import pull_job

        pull_job(job_id, subdirs or ["input", "templates", "output", "consents", "reports", "archives"])
    except ValueError:
        pass


def _run_philologist_background(*, ai_enabled: bool, job_id: str | None, mode: str | None) -> None:
    _sync_job_pull(job_id)
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
    _sync_job_pull(job_id)
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


def _large_upload_verification_limit_exceeded(job_id: str | None, source: str) -> tuple[int, int] | None:
    if source != "upload" or not job_id:
        return None
    limit_bytes = int(getattr(settings, "municipality_upload_auto_verify_max_bytes", 0) or 0)
    if limit_bytes <= 0:
        return None
    data_xlsx_path = resolve_job_paths(job_id).data_xlsx
    try:
        file_size_bytes = data_xlsx_path.stat().st_size
    except OSError:
        return None
    if file_size_bytes <= limit_bytes:
        return None
    return file_size_bytes, limit_bytes

def _start_parser_verification_process(*, job_id: str | None, filename: str, source: str = "upload") -> None:
    started_at = perf_counter()
    key = _parser_job_key(job_id)
    with _parser_verification_threads_lock:
        existing_thread = _parser_verification_threads.get(key)
        if existing_thread and existing_thread.is_alive():
            logger.info(
                "upload_data_verification_already_running",
                filename=filename,
                job_id=job_id,
                worker_name=existing_thread.name,
            )
            return
        if existing_thread and not existing_thread.is_alive():
            _parser_verification_threads.pop(key, None)

        skipped_upload = _large_upload_verification_limit_exceeded(job_id, source)
        if skipped_upload is not None:
            file_size_bytes, limit_bytes = skipped_upload
            reason = (
                f"файл {file_size_bytes} байт больше лимита автопроверки {limit_bytes} байт"
            )
            mark_municipality_verification_skipped(
                job_id,
                source=source,
                reason=reason,
                file_size_bytes=file_size_bytes,
                limit_bytes=limit_bytes,
            )
            logger.info(
                "upload_data_verification_skipped_large_file",
                filename=filename,
                job_id=job_id,
                file_size_bytes=file_size_bytes,
                limit_bytes=limit_bytes,
                schedule_seconds=round(perf_counter() - started_at, 3),
            )
            return

        prime_municipality_verification_running(
            job_id,
            source=source,
            summary_text="Файл загружен. Проверяю официальные названия МО.",
        )
        if bool(settings.background_queue_enabled):
            from src.workers.task_queue import QueueTaskHandle, enqueue_task

            owner = read_job_owner(job_id)
            queued_task, _ = enqueue_task(
                task_type="parser_verification",
                job_id=job_id,
                payload={"job_id": job_id, "source": source},
                owner_username=str(owner.get("owner_username") or ""),
                max_attempts=max(1, int(settings.background_queue_max_attempts or 3)),
            )
            verification_thread = QueueTaskHandle(
                str(queued_task["id"]),
                name=f"parser-verify-{key}",
            )
            _parser_verification_threads[key] = verification_thread  # type: ignore[assignment]
        else:
            verification_thread = threading.Thread(
                target=_run_parser_verification_background,
                kwargs={"job_id": job_id, "source": source},
                daemon=True,
                name=f"parser-verify-{key}",
            )
            _parser_verification_threads[key] = verification_thread
            verification_thread.start()
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


FRONTEND_DIST = Path(
    str(getattr(settings, "frontend_dist_dir", "") or "").strip()
    or (PROJECT_ROOT / "frontend" / "dist")
)
def _spa_index_response() -> HTMLResponse | FileResponse | RedirectResponse:
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=503, detail="Frontend SPA is not built.")
    return FileResponse(
        index_file,
        headers={
            "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/", response_class=HTMLResponse)
def index(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    username = get_session_username(session_token, ttl_days=max(1, int(settings.app_session_ttl_days or 7)))
    if not username or get_user_record(username) is None:
        return RedirectResponse(url="/login", status_code=303)
    return _spa_index_response()


@app.get("/api/status")
def app_status(principal: object = Depends(check_auth)):
    return {"status": "ok", "message": "Сервер работает"}

from src.generator.generation.excel_io import load_rows
from src.generator.generation.transforms import build_document_context
from src.generator.generation.document_builder import OUTPUT_FOLDER_MANIFEST_FILENAME, cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.generation.config_generator import (
    BATCH_PDF_DIR,
    DOCX_WORKERS,
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
    build_sender_delivery_analytics,
)
from src.generator.delivery.mailopost_events import append_mailopost_events
from src.generator.delivery.rusender_events import append_rusender_events
from src.generator.delivery.unisender_go_events import append_unisender_go_events
from src.generator.orchestration.parser_agent import (
    _load_parser_state,
    _save_parser_state,
    format_municipality_verification_for_chat,
    get_parser_status,
    mark_municipality_verification_failed,
    mark_municipality_verification_skipped,
    prime_municipality_verification_running,
    run_parser_agent,
    run_parser_municipality_verification,
)
from src.generator.orchestration.orchestrator_agent import (
    chat_with_orchestrator,
)
from src.generator.orchestration.autonomous_worker import (
    get_autonomous_worker_state,
)
from src.generator.case_engine.overrides import upsert_override
from src.generator.generation.generator_agent import (
    _load_generator_state,
    _save_generator_state,
    clear_generator_stop_request,
    finalize_output_pdfs_for_job,
    get_generator_status,
    prime_generator_state,
    request_generator_stop,
    run_generator_agent,
)
from src.generator.philologist.philologist_planner import build_philologist_plan
from src.jobs import create_job_id, normalize_job_id, resolve_job_paths
from src.jobs.storage import JOBS_DIR


def cleanup_batch_pdf_dir() -> None:
    if BATCH_PDF_DIR.exists():
        shutil.rmtree(BATCH_PDF_DIR)
    BATCH_PDF_DIR.mkdir(parents=True, exist_ok=True)


def process_web_row(payload: tuple[int, int, dict]) -> dict:
    result_index, outgoing_number, row = payload
    row_id = row.get("ID")
    mun_name = row.get("MUN_NAME") or row.get("MUN_R_NAME") or "unknown"
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


jobs_controller = JobsWebController(
    check_auth=check_auth,
    settings=settings,
    logger=logger,
    prefer_existing_file=_prefer_existing_file,
    validate_uploaded_file=_validate_uploaded_file,
    cached_excel_row_count=_cached_excel_row_count,
    cached_tree_file_count=_cached_tree_file_count,
    safe_int=_safe_int,
    create_job_id=create_job_id,
    resolve_job_paths=resolve_job_paths,
    jobs_dir=JOBS_DIR,
    create_documents_load_test_job=create_documents_load_test_job,
    start_parser_verification_process=_start_parser_verification_process,
    get_parser_status=get_parser_status,
    get_generator_status=get_generator_status,
    get_philologist_status=get_philologist_status,
    get_sender_status=get_sender_status,
    run_parser_municipality_verification=run_parser_municipality_verification,
)


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
    finalize_documents_output=finalize_output_pdfs_for_job,
    load_generator_state=_load_generator_state,
    load_philologist_state=_load_philologist_state,
    schedule_output_archive_build=_schedule_output_archive_build,
    unregister_documents_thread=_unregister_documents_thread,
    build_job_readiness_result=jobs_controller.build_job_readiness_result,
    chat_with_orchestrator=chat_with_orchestrator,
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
    create_auth_router(
        settings_obj=settings,
        check_auth=check_auth,
        spa_index_path=FRONTEND_DIST / "index.html",
    )
)
app.include_router(create_v1_router(check_auth=check_auth))
app.include_router(create_companies_router(check_auth=check_auth))
app.include_router(jobs_controller.router)
app.include_router(create_consent_router())
app.include_router(create_chain_router())
app.include_router(
    create_workers_router(
        check_auth=check_auth,
        jobs_dir=JOBS_DIR,
        list_worker_statuses=list_worker_statuses,
        terminate_worker_process=terminate_worker_process,
    )
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
        prime_sender_queued_state=prime_sender_queued_state,
        prime_sender_scheduled_state=prime_sender_scheduled_state,
        start_sender_thread_if_absent=_start_sender_thread_if_absent,
        run_sender_background=run_sender_background,
        sender_job_key=_sender_job_key,
        get_sender_status=get_sender_status,
        get_generator_status=get_generator_status,
        get_unisender_history=get_unisender_history,
        build_sender_delivery_analytics=build_sender_delivery_analytics,
        settings=settings,
        append_unisender_go_events=append_unisender_go_events,
        append_rusender_events=append_rusender_events,
        append_mailopost_events=append_mailopost_events,
        logger=logger,
        request_sender_stop=request_sender_stop,
        preview_recipients=preview_recipients,
        chat_with_sender=chat_with_sender,
        is_load_test_job=is_load_test_job,
    )
)

app.include_router(
    create_smtp_router(
        check_auth=check_auth,
    )
)

app.include_router(
    create_statistics_router(
        check_auth=check_auth,
        jobs_dir=JOBS_DIR,
        resolve_job_paths=resolve_job_paths,
        logger=logger,
    )
)

app.include_router(
    create_download_router(
        check_auth=check_auth,
        prefer_existing_file=_prefer_existing_file,
        latest_matching_file=_latest_matching_file,
        resolve_cached_output_archive=_resolve_cached_output_archive,
        build_output_archive=_build_output_archive,
        is_cache_fresh=_is_cache_fresh,
        job_state_dir=_job_state_dir,
        get_parser_status=get_parser_status,
        safe_int=_safe_int,
        output_archive_ready=lambda job_id: bool(compact_documents_status(job_id).get("output_ready")),
    )
)

app.include_router(
    create_preview_router(
        check_auth=check_auth,
        latest_matching_file=_latest_matching_file,
        is_cache_fresh=_is_cache_fresh,
        job_state_dir=_job_state_dir,
        get_parser_status=get_parser_status,
        safe_int=_safe_int,
        resolve_cached_output_archive=_resolve_cached_output_archive,
        build_output_archive=_build_output_archive,
        output_archive_ready=lambda job_id: bool(compact_documents_status(job_id).get("output_ready")),
    )
)

app.include_router(create_public_router())

app.include_router(
    create_generator_router(
        check_auth=check_auth,
        job_readiness=jobs_controller.job_readiness,
        prefer_existing_file=_prefer_existing_file,
        resolve_job_paths=resolve_job_paths,
        get_generator_thread=_get_generator_thread,
        compact_generator_status=_compact_generator_status,
        get_generator_status=get_generator_status,
        clear_generator_stop_request=clear_generator_stop_request,
        prime_generator_state=prime_generator_state,
        schedule_output_archive_build=_schedule_output_archive_build,
        run_generator_background=_run_generator_background,
        generator_job_key=_generator_job_key,
        register_generator_thread=_register_generator_thread,
        request_generator_stop=request_generator_stop,
        ensure_user_inprocess_limit=_ensure_generator_user_limit,
        start_generator_task=_start_generator_task,
    )
)

app.include_router(
    create_parser_router(
        check_auth=check_auth,
        parse_optional_limit=_parse_optional_limit,
        start_parser_thread_if_absent=_start_parser_thread_if_absent,
        parser_job_key=_parser_job_key,
        get_parser_thread=_get_parser_thread,
        run_parser_agent=run_parser_agent,
        get_parser_status=get_parser_status,
        run_parser_municipality_verification=run_parser_municipality_verification,
        format_municipality_verification_for_chat=format_municipality_verification_for_chat,
        parser_progress_subscribe=parser_progress_subscribe,
        logger=logger,
    )
)

app.include_router(
    create_philologist_router(
        check_auth=check_auth,
        get_philologist_thread=_get_philologist_thread,
        compact_philologist_status=_compact_philologist_status,
        get_philologist_status=lambda job_id: get_philologist_status(job_id, include_details=False),
        start_philologist_task=_start_philologist_task,
        clear_philologist_stop_request=clear_philologist_stop_request,
        prime_philologist_running_state=_prime_philologist_running_state,
        run_philologist_background=_run_philologist_background,
        philologist_job_key=_philologist_job_key,
        register_philologist_thread=_register_philologist_thread,
        request_philologist_stop=request_philologist_stop,
        build_philologist_plan=build_philologist_plan,
        chat_with_philologist=chat_with_philologist,
        ensure_user_inprocess_limit=_ensure_philologist_user_limit,
    )
)

app.include_router(
    create_agent_router(
        check_auth=check_auth,
        upsert_override=upsert_override,
        get_autonomous_worker_state=get_autonomous_worker_state,
    )
)



# Serve built React SPA assets when present (same origin as API on :9806).
_assets_dir = FRONTEND_DIST / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="frontend-assets")


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa_fallback(full_path: str, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    """SPA client-side routes; keep API/public/consent paths untouched."""
    blocked_prefixes = (
        "api/",
        "public/",
        "consent/",
        "login",
        "register",
        "legacy",
        "health",
        "assets/",
        "docs",
        "openapi.json",
        "redoc",
    )
    if full_path.startswith(blocked_prefixes) or full_path in {"health", "docs", "redoc", "openapi.json"}:
        raise HTTPException(status_code=404, detail="Not Found")
    if not (FRONTEND_DIST / "index.html").exists():
        raise HTTPException(status_code=404, detail="Not Found")
    # Auth pages are served by auth_router; app shell requires session except public SPA shells.
    return _spa_index_response()


if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск сервера", host=settings.app_host, port=settings.app_port)
    uvicorn.run("main:app", host=settings.app_host, port=settings.app_port)
