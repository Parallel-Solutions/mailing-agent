import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = DATA_DIR / "templates"
OUTPUT_DIR = DATA_DIR / "output"
RUN_ID = os.environ.get("KP_RUN_ID", "default")
BATCH_DOCX_DIR = DATA_DIR / f"_batch_docx_{RUN_ID}"
BATCH_PDF_DIR = DATA_DIR / f"_batch_pdf_{RUN_ID}"
BATCH_LIBREOFFICE_PROFILES_DIR = DATA_DIR / f"_libreoffice_profiles_{RUN_ID}"

# Локальная копия входного Excel. Позже можно заменить на путь/том сервера.
DATA_XLSX_PATH = DATA_DIR / "data.xlsx"

# Стартовые номера для документов.
START_OUTGOING_NUMBER = 101

# Базовые настройки отправки.
SEND_DELAY_MIN_SECONDS = 20
SEND_DELAY_MAX_SECONDS = 40

# Производительность
DOCX_WORKERS = max(1, min(6, (os.cpu_count() or 2) - 1))
PDF_WORKERS = 1
PDF_CHUNK_SIZE = 100
BENCHMARK_ROW_LIMIT = 10


def _read_env_override(key_name: str, default: str) -> str:
    direct_value = os.environ.get(key_name)
    if direct_value is not None:
        return direct_value

    for env_path in (BASE_DIR / ".env", BASE_DIR / ".env.local"):
        try:
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                raw_key, raw_value = line.split("=", 1)
                if raw_key.strip() == key_name:
                    return raw_value.strip().strip('"').strip("'")
        except OSError:
            continue
    return default


# AI-агент проверки падежей
ENABLE_CASE_AGENT = _read_env_override("ENABLE_CASE_AGENT", "0") == "1"
CASE_AGENT_MODE = _read_env_override("CASE_AGENT_MODE", "dry_run")  # dry_run | auto_fix
CASE_AGENT_MODEL = _read_env_override("CASE_AGENT_MODEL", "gpt-4o-mini")
CASE_AGENT_ONLY_SUSPICIOUS = _read_env_override("CASE_AGENT_ONLY_SUSPICIOUS", "0") == "1"
CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE = float(_read_env_override("CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE", "0.9"))
CASE_AGENT_OK_DEFAULT_CONFIDENCE = float(_read_env_override("CASE_AGENT_OK_DEFAULT_CONFIDENCE", "0.8"))

# AI-ревью готового документа
ENABLE_DOCUMENT_REVIEW_AI = _read_env_override("ENABLE_DOCUMENT_REVIEW_AI", "1") == "1"
DOCUMENT_REVIEW_MODEL = _read_env_override("DOCUMENT_REVIEW_MODEL", CASE_AGENT_MODEL)
WEB_CASE_AGENT_MAX_WORKERS = max(1, int(_read_env_override("WEB_CASE_AGENT_MAX_WORKERS", "1")))
