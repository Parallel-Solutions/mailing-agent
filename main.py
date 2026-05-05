from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets
from src.utils.logger import logger
from src.utils.config import settings
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File
import shutil

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
async def status(username: str = Depends(check_auth)):
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

from src.generator.excel_io import load_rows
from src.generator.transforms import build_document_context
from src.generator.document_builder import generate_documents_for_row
from src.generator.config_generator import START_OUTGOING_NUMBER

@app.post("/api/generate")
async def generate(username: str = Depends(check_auth)):
    xlsx_path = Path("data/data.xlsx")
    if not xlsx_path.exists():
        raise HTTPException(status_code=400, detail="Файл data.xlsx не найден")
    _, _, rows = load_rows(xlsx_path)
    if not rows:
        raise HTTPException(status_code=400, detail="Нет данных в файле")
    results = []
    for i, row in enumerate(rows):
        try:
            context = build_document_context(row, START_OUTGOING_NUMBER + i)
            files = generate_documents_for_row(row, context)
            results.append({"id": row.get("ID"), "status": "ok", "files": [str(v) for v in files.values()]})
        except Exception as e:
            results.append({"id": row.get("ID"), "status": "error", "error": str(e)})
    return {"total": len(results), "results": results}


from fastapi.responses import FileResponse
import zipfile
import tempfile

@app.get("/api/download/output")
async def download_output(username: str = Depends(check_auth)):
    batch_dir = Path("data/_batch_docx_default")
    if not batch_dir.exists() or not list(batch_dir.glob("*.docx")):
        raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")
    
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in batch_dir.glob("*.docx"):
            zf.write(f, f.name)
    
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename="output.zip"
    )


if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск сервера", host=settings.app_host, port=settings.app_port)
    uvicorn.run("main:app", host=settings.app_host, port=settings.app_port, reload=True)