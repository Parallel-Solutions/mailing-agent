import os
from pathlib import Path

from src.utils.env import resolve_env_value


BASE_DIR = Path(__file__).resolve().parents[3]
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
BENCHMARK_ROW_LIMIT = 10


def _read_env_override(key_name: str, default: str) -> str:
    return resolve_env_value(key_name, default) or default


PDF_WORKERS = max(1, int(_read_env_override("PDF_WORKERS", "3")))
PDF_CHUNK_SIZE = max(1, int(_read_env_override("PDF_CHUNK_SIZE", "10")))


# AI-агент проверки падежей
ENABLE_CASE_AGENT = _read_env_override("ENABLE_CASE_AGENT", "0") == "1"
CASE_AGENT_MODE = _read_env_override("CASE_AGENT_MODE", "dry_run")  # dry_run | auto_fix
CASE_AGENT_MODEL = _read_env_override("CASE_AGENT_MODEL", "gpt-4o-mini")
CASE_AGENT_ONLY_SUSPICIOUS = _read_env_override("CASE_AGENT_ONLY_SUSPICIOUS", "0") == "1"
CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE = float(_read_env_override("CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE", "0.9"))
CASE_AGENT_OK_DEFAULT_CONFIDENCE = float(_read_env_override("CASE_AGENT_OK_DEFAULT_CONFIDENCE", "0.8"))
AGENT_MEMORY_AUTO_APPROVE_SAFE_INFLECTIONS = (
    _read_env_override("AGENT_MEMORY_AUTO_APPROVE_SAFE_INFLECTIONS", "1") == "1"
)
ENABLE_SEMANTIC_RAG = _read_env_override("ENABLE_SEMANTIC_RAG", "0") == "1"
RAG_EMBEDDING_MODEL = _read_env_override("RAG_EMBEDDING_MODEL", "cointegrated/rubert-tiny").strip()
RAG_SEMANTIC_MIN_SCORE = float(_read_env_override("RAG_SEMANTIC_MIN_SCORE", "0.45"))
RAG_SEMANTIC_WEIGHT = int(_read_env_override("RAG_SEMANTIC_WEIGHT", "30"))

# AI-ревью готового документа
ENABLE_DOCUMENT_REVIEW_AI = _read_env_override("ENABLE_DOCUMENT_REVIEW_AI", "1") == "1"
DOCUMENT_REVIEW_MODEL = _read_env_override("DOCUMENT_REVIEW_MODEL", CASE_AGENT_MODEL)
PHILOLOGIST_MODE = _read_env_override("PHILOLOGIST_MODE", "fast").strip().lower() or "fast"
PHILOLOGIST_LLM_ROUTER = _read_env_override("PHILOLOGIST_LLM_ROUTER", "0") == "1"
PHILOLOGIST_LLM_FIX_STRATEGY = _read_env_override("PHILOLOGIST_LLM_FIX_STRATEGY", "0") == "1"
PHILOLOGIST_REBUILD_PDF = _read_env_override("PHILOLOGIST_REBUILD_PDF", "0") == "1"
PHILOLOGIST_DOC_TIMEOUT_SECONDS = max(30, int(_read_env_override("PHILOLOGIST_DOC_TIMEOUT_SECONDS", "180")))
PHILOLOGIST_CONTEXT_LLM = _read_env_override("PHILOLOGIST_CONTEXT_LLM", "1") == "1"
PHILOLOGIST_CONTEXT_LLM_MAX_ITEMS = max(0, int(_read_env_override("PHILOLOGIST_CONTEXT_LLM_MAX_ITEMS", "60")))
PHILOLOGIST_CONTEXT_LLM_BATCH_SIZE = max(1, int(_read_env_override("PHILOLOGIST_CONTEXT_LLM_BATCH_SIZE", "12")))
PHILOLOGIST_CONTEXT_LLM_MIN_CONFIDENCE = float(_read_env_override("PHILOLOGIST_CONTEXT_LLM_MIN_CONFIDENCE", "0.85"))
WEB_CASE_AGENT_MAX_WORKERS = max(1, int(_read_env_override("WEB_CASE_AGENT_MAX_WORKERS", "1")))

# PDF-конвертация
KP_GENERATION_ENGINE = _read_env_override("KP_GENERATION_ENGINE", "template").strip().lower() or "template"
_GOTENBERG_BASE_URLS_RAW = _read_env_override(
    "GOTENBERG_BASE_URLS",
    _read_env_override("GOTENBERG_BASE_URL", ""),
)
GOTENBERG_BASE_URLS = tuple(
    url.strip().rstrip("/")
    for url in _GOTENBERG_BASE_URLS_RAW.split(",")
    if url.strip()
)
GOTENBERG_CONVERT_TIMEOUT_SECONDS = float(_read_env_override("GOTENBERG_CONVERT_TIMEOUT_SECONDS", "300"))
ONLYOFFICE_BASE_URL = ""
ONLYOFFICE_CONVERTER_MODE = "url"
ONLYOFFICE_CONVERT_TIMEOUT_SECONDS = 120.0
ONLYOFFICE_JWT_SECRET = ""
ONLYOFFICE_PUBLIC_FILES_DIR = DATA_DIR / "_onlyoffice_public"
ONLYOFFICE_PUBLIC_FILES_URL = ""
LIBREOFFICE_CONVERT_TIMEOUT_SECONDS = float(_read_env_override("LIBREOFFICE_CONVERT_TIMEOUT_SECONDS", "180"))

