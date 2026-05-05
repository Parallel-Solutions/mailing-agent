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


@app.post("/api/upload/template")
async def upload_template(file: UploadFile = File(...), username: str = Depends(check_auth)):
    dest = Path("data/templates") / file.filename
    dest.parent.mkdir(exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"status": "ok", "filename": file.filename}

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
    
    sender_total = generator_total // 2
    
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
    _, _, rows = load_rows(xlsx_path)
    if not rows:
        raise HTTPException(status_code=400, detail="Нет данных в файле")

    cleanup_batch_docx_dir()
    cleanup_batch_pdf_dir()

    started_at = perf_counter()
    logger.info("web_generate_start", row_count=len(rows))
    results = []
    payloads = [
        (index, START_OUTGOING_NUMBER + index, row)
        for index, row in enumerate(rows)
    ]
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

    if ENABLE_CASE_AGENT:
        logger.info(
            "web_generate_dispatch",
            row_count=len(payloads),
            execution_mode="sequential_case_agent",
            max_workers=1,
            case_agent_enabled=ENABLE_CASE_AGENT,
            case_agent_only_suspicious=CASE_AGENT_ONLY_SUSPICIOUS,
            web_case_agent_max_workers=WEB_CASE_AGENT_MAX_WORKERS,
        )
        completed_count = 0
        for payload in payloads:
            result_index, _, row = payload
            try:
                results[result_index] = process_web_row(payload)
                completed_count += 1
                logger.info(
                    "web_generate_row_done",
                    completed=completed_count,
                    total=len(payloads),
                    row_id=row.get("ID"),
                    status=results[result_index].get("status"),
                    case_agent_status=results[result_index].get("case_agent_status"),
                )
            except Exception as e:
                completed_count += 1
                results[result_index] = {
                    "result_index": result_index,
                    "id": row.get("ID"),
                    "status": "error",
                    "error": str(e),
                    "files": {},
                }
                logger.exception(
                    "web_generate_row_failed",
                    completed=completed_count,
                    total=len(payloads),
                    row_id=row.get("ID"),
                )
    else:
        max_workers = max(1, min(DOCX_WORKERS, len(payloads)))
        logger.info(
            "web_generate_dispatch",
            row_count=len(payloads),
            execution_mode="process_pool",
            max_workers=max_workers,
            case_agent_enabled=ENABLE_CASE_AGENT,
            case_agent_only_suspicious=CASE_AGENT_ONLY_SUSPICIOUS,
            web_case_agent_max_workers=WEB_CASE_AGENT_MAX_WORKERS,
        )
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(process_web_row, payload): payload
                for payload in payloads
            }
            completed_count = 0
            for future in as_completed(future_map):
                result_index, _, row = future_map[future]
                try:
                    results[result_index] = future.result()
                    completed_count += 1
                    logger.info(
                        "web_generate_row_done",
                        completed=completed_count,
                        total=len(payloads),
                        row_id=row.get("ID"),
                        status=results[result_index].get("status"),
                        case_agent_status=results[result_index].get("case_agent_status"),
                    )
                except Exception as e:
                    completed_count += 1
                    results[result_index] = {
                        "result_index": result_index,
                        "id": row.get("ID"),
                        "status": "error",
                        "error": str(e),
                        "files": {},
                    }
                    logger.exception(
                        "web_generate_row_failed",
                        completed=completed_count,
                        total=len(payloads),
                        row_id=row.get("ID"),
                    )

    finalize_generated_files(results)
    for result in results:
        result.pop("result_index", None)

    logger.info(
        "web_generate_done",
        total=len(results),
        ok_count=sum(1 for item in results if item.get("status") == "ok"),
        error_count=sum(1 for item in results if item.get("status") == "error"),
        elapsed_seconds=round(perf_counter() - started_at, 2),
    )
    return {"total": len(results), "results": results}


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


if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск сервера", host=settings.app_host, port=settings.app_port)
    uvicorn.run("main:app", host=settings.app_host, port=settings.app_port, reload=True)
