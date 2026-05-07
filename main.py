from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets
from src.utils.logger import logger
from src.utils.config import settings
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Body
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from time import perf_counter

app = FastAPI(title="Mailing Agent")
security = HTTPBasic()


@app.on_event("startup")
async def app_startup():
    start_autonomous_worker()


@app.on_event("shutdown")
async def app_shutdown():
    stop_autonomous_worker()


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


@app.get("/", response_class=HTMLResponse)
async def index(username: str = Depends(check_auth)):
    return Path("templates/index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def app_status(username: str = Depends(check_auth)):
    return {"status": "ok", "message": "Сервер работает"}

@app.post("/api/upload/data")
async def upload_data(file: UploadFile = File(...), username: str = Depends(check_auth)):
    dest = Path("data/data.xlsx")
    dest.parent.mkdir(exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"status": "ok", "filename": file.filename}


@app.get("/api/data/info")
async def data_info(username: str = Depends(check_auth)):
    data_path = Path("data/data.xlsx")
    if not data_path.exists():
        return {"loaded": False, "total": 0}
    _, _, rows = load_rows(data_path)
    return {"loaded": True, "total": len(rows)}


@app.post("/api/upload/template")
async def upload_template(file: UploadFile = File(...), username: str = Depends(check_auth)):
    templates_dir = Path("data/templates")
    templates_dir.mkdir(exist_ok=True)
    original_name = Path(file.filename or "").name
    if original_name.lower().endswith(".txt"):
        dest = templates_dir / "mail_template.txt"
    else:
        dest = templates_dir / original_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "status": "ok",
        "filename": file.filename,
        "stored_as": dest.name,
    }

@app.post("/api/upload/base")
async def upload_base(file: UploadFile = File(...), username: str = Depends(check_auth)):
    dest = Path("data/base.xlsx")
    dest.parent.mkdir(exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"status": "ok", "filename": file.filename}

from src.generator.excel_io import load_rows
from src.generator.transforms import build_document_context
from src.generator.document_builder import cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.config_generator import (
    BATCH_PDF_DIR,
    DOCX_WORKERS,
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
    chat_with_sender,
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
async def counts(username: str = Depends(check_auth)):
    base_path = Path("data/base.xlsx")
    data_path = Path("data/data.xlsx")
    
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
async def generate(username: str = Depends(check_auth)):
    xlsx_path = Path("data/data.xlsx")
    if not xlsx_path.exists():
        raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")
    result = run_generator_agent(xlsx_path=xlsx_path)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("summary_text") or "Ошибка генерации")
    return {
        "total": len(result.get("results", [])),
        "results": result.get("results", []),
        "summary_text": result.get("summary_text"),
        "task_stats": result.get("task_stats"),
        "recent_events": result.get("recent_events"),
    }


@app.get("/api/generator/status")
async def generator_status(username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_generator_status()}


from fastapi.responses import FileResponse
import zipfile
import tempfile

@app.get("/api/download/output")
async def download_output(username: str = Depends(check_auth)):
    output_dir = Path("data/output")
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
async def download_data_xlsx(username: str = Depends(check_auth)):
    data_path = Path("data/data.xlsx")
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="Файл data.xlsx не найден.")
    return FileResponse(
        data_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="data.xlsx",
    )


@app.get("/api/download/sent-mail-log")
async def download_sent_mail_log(username: str = Depends(check_auth)):
    log_path = Path("data/sent_mail_log.jsonl")
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
    result = run_philologist(ai_enabled=ai_enabled)
    return {"status": "ok", "result": result}


@app.get("/api/philologist/status")
async def philologist_status(username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_philologist_status()}


@app.post("/api/philologist/chat")
async def philologist_chat(
    payload: dict = Body(...),
    username: str = Depends(check_auth),
):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    return {"status": "ok", **chat_with_philologist(message)}

from src.parser.agent import chat, clear_memory, get_memory, run_batch_parser, set_system_prompt

@app.post("/api/parser/chat")
async def parser_chat(payload: dict = Body(...), username: str = Depends(check_auth)):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    result = chat(message)
    return result

@app.post("/api/parser/start")
async def parser_start(username: str = Depends(check_auth)):
    result = run_batch_parser()
    return result

@app.get("/api/parser/memory")
async def parser_memory(username: str = Depends(check_auth)):
    return get_memory()

@app.post("/api/parser/memory/clear")
async def parser_memory_clear(username: str = Depends(check_auth)):
    clear_memory()
    return {"status": "ok"}

@app.post("/api/parser/prompt")
async def parser_prompt(payload: dict = Body(...), username: str = Depends(check_auth)):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Пустой промпт")
    set_system_prompt(prompt)
    return {"status": "ok"}


@app.post("/api/sender/run")
async def sender_run(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    dry_run = True if payload is None else bool(payload.get("dry_run", True))
    limit = _parse_optional_limit(payload)
    transport = None if payload is None else payload.get("transport")
    result = run_sender(dry_run=dry_run, limit=limit, transport=transport)
    return {"status": "ok", "result": result}


@app.get("/api/sender/status")
async def sender_status(username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_sender_status()}


@app.post("/api/sender/stop")
async def sender_stop(username: str = Depends(check_auth)):
    result = request_sender_stop()
    return {"status": "ok", "result": result}


@app.post("/api/sender/preview")
async def sender_preview(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    limit = _parse_optional_limit(payload)
    result = preview_recipients(limit=limit)
    return {"status": "ok", "result": result}


@app.post("/api/sender/chat")
async def sender_chat(
    payload: dict = Body(...),
    username: str = Depends(check_auth),
):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    return {"status": "ok", **chat_with_sender(message)}


@app.post("/api/parser/run")
async def parser_run(
    payload: dict | None = Body(default=None),
    username: str = Depends(check_auth),
):
    limit = _parse_optional_limit(payload)
    result = run_parser_agent(limit=limit)
    return {"status": "ok", "result": result}


@app.get("/api/parser/status")
async def parser_status(username: str = Depends(check_auth)):
    return {"status": "ok", "result": get_parser_status()}

@app.post("/api/upload/rmz")
async def upload_rmz(file: UploadFile = File(...), username: str = Depends(check_auth)):
    dest = Path("data/RMZ7KH.xlsx")
    dest.parent.mkdir(exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"status": "ok", "filename": file.filename}

@app.post("/api/parser/merge-rmz")
async def merge_rmz(username: str = Depends(check_auth)):
    from src.parser.rmz_merger import run_merge
    result = run_merge()
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
            f"Проверь эти спорные совпадения из слияния RMZ7KH. "
            f"Для каждого скажи верное ли совпадение: {suspicious_list}"
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
    return {"status": "ok", **chat_with_parser(message)}


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
