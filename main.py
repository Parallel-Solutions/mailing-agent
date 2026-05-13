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

app = FastAPI(title="Mailing Agent")
security = HTTPBasic()
_sender_threads: dict[str, threading.Thread] = {}
_sender_threads_lock = threading.Lock()


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


def _get_sender_thread(job_id: str | None) -> threading.Thread | None:
    key = _sender_job_key(job_id)
    with _sender_threads_lock:
        thread = _sender_threads.get(key)
        if thread and not thread.is_alive():
            _sender_threads.pop(key, None)
            return None
        return thread


def _register_sender_thread(job_id: str | None, thread: threading.Thread) -> None:
    with _sender_threads_lock:
        _sender_threads[_sender_job_key(job_id)] = thread


def _run_sender_background(*, limit: int | None, transport: str | None, job_id: str | None) -> None:
    try:
        run_sender(dry_run=False, limit=limit, transport=transport, auto_recover=False, job_id=job_id)
    except Exception:
        logger.exception("sender_background_failed", job_id=job_id, transport=transport)
    finally:
        with _sender_threads_lock:
            _sender_threads.pop(_sender_job_key(job_id), None)


def _prime_sender_running_state(job_id: str | None, transport: str | None) -> dict:
    state = _load_sender_state(job_id)
    state["status"] = "running"
    state["mode"] = "send"
    state["transport"] = transport or state.get("transport") or "smtp"
    state["summary_text"] = "Агент-отправщик начал отправку писем."
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _save_sender_state(state, job_id)
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
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"status": "ok", "filename": file.filename, "job_id": paths.job_id}


@app.get("/api/data/info")
async def data_info(job_id: str | None = None, username: str = Depends(check_auth)):
    data_path = _prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
    if not data_path.exists():
        return {"loaded": False, "total": 0}
    _, _, rows = load_rows(data_path)
    return {"loaded": True, "total": len(rows)}


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

from src.generator.excel_io import load_rows
from src.generator.transforms import build_document_context
from src.generator.document_builder import cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.config_generator import (
    BATCH_PDF_DIR,
    DOCX_WORKERS,
    ONLYOFFICE_PUBLIC_FILES_DIR,
    START_OUTGOING_NUMBER,
    WEB_CASE_AGENT_MAX_WORKERS,
)
from src.generator.pdf_converter import convert_docx_batch
from src.generator.ai_case_agent import (
    ENABLE_CASE_AGENT,
    CASE_AGENT_ONLY_SUSPICIOUS,
    apply_case_agent_result,
    run_case_validation_agent,
)
from src.generator.philologist_agent import (
    chat_with_philologist,
    get_philologist_status,
    run_philologist,
)
from src.generator.sender_agent import (
    _load_sender_state,
    _save_sender_state,
    chat_with_sender,
    clear_sender_stop_request,
    get_sender_status,
    preview_recipients,
    request_sender_stop,
    run_sender,
)
from src.generator.parser_agent import (
    chat_with_parser,
    get_parser_status,
    run_parser_agent,
)
from src.generator.orchestrator_agent import (
    chat_with_orchestrator,
    get_orchestrator_status,
)
from src.generator.autonomous_worker import (
    get_autonomous_worker_state,
    start_autonomous_worker,
    stop_autonomous_worker,
)
from src.generator.generator_agent import get_generator_status, run_generator_agent
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

    sender_total = generator_total
    
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
    result = run_generator_agent(xlsx_path=xlsx_path, job_id=job_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("summary_text") or "Ошибка генерации")
    if int(result.get("ok_rows", 0) or 0) == 0 and int(result.get("error_rows", 0) or 0) > 0:
        raise HTTPException(status_code=400, detail=result.get("summary_text") or "Генерация не создала ни одного комплекта документов")
    return {
        "status": result.get("status"),
        "total": len(result.get("results", [])),
        "total_rows": result.get("total_rows"),
        "processed_rows": result.get("processed_rows"),
        "ok_rows": result.get("ok_rows"),
        "error_rows": result.get("error_rows"),
        "results": result.get("results", []),
        "summary_text": result.get("summary_text"),
        "task_stats": result.get("task_stats"),
        "recent_events": result.get("recent_events"),
    }


@app.get("/api/generator/status")
async def generator_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_generator_status(job_id)}


from fastapi.responses import FileResponse
import zipfile
import tempfile

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
    
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(output_dir))
    
    return FileResponse(
        tmp.name,
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


@app.post("/api/philologist/run")
async def philologist_run(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    ai_enabled = True if payload is None else bool(payload.get("ai_enabled", True))
    job_id = None if payload is None else str(payload.get("job_id") or "").strip() or None
    result = run_philologist(ai_enabled=ai_enabled, job_id=job_id)
    return {"status": "ok", "result": result}


@app.get("/api/philologist/status")
async def philologist_status(job_id: str | None = None, username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_philologist_status(job_id)}


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
    result = run_batch_parser(job_id=job_id)
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
    return {"status": "ok", **chat_with_parser(message, job_id=job_id)}


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
