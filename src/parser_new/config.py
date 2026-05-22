"""
config.py — центральный конфиг проекта.
Загружает переменные из .env и предоставляет их всему проекту.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv()


# ==============================
# ПУТИ
# ==============================
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "output")
MEMORY_DIR = BASE_DIR / os.getenv("MEMORY_DIR", "memory")
LOG_FILE   = BASE_DIR / os.getenv("LOG_FILE", "logs/agent.log")

# Создаём папки если их нет
for folder in [
    OUTPUT_DIR / "latest",
    OUTPUT_DIR / "archive",
    MEMORY_DIR / "vectors",
    LOG_FILE.parent,
]:
    folder.mkdir(parents=True, exist_ok=True)


# ==============================
# МОЗГ АГЕНТА
# ==============================
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
LLM_BASE_URL       = os.getenv("LLM_BASE_URL", "")   # свой URL если есть
AGENT_MODEL        = os.getenv("AGENT_MODEL", "claude-sonnet-4-20250514")
AGENT_MAX_ITER     = int(os.getenv("AGENT_MAX_ITERATIONS", 10))
AGENT_VERBOSE      = os.getenv("AGENT_VERBOSE", "true").lower() == "true"


# ==============================
# API КЛЮЧИ ИНСТРУМЕНТОВ
# ==============================
TAVILY_API_KEY  = os.getenv("TAVILY_API_KEY", "")
CHECKO_API_KEY  = os.getenv("CHECKO_API_KEY", "")
TWOGIS_API_KEY  = os.getenv("TWOGIS_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
YANDEX_API_KEY   = os.getenv("YANDEX_API_KEY", "")


# ==============================
# ПАМЯТЬ
# ==============================
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379")
SQLITE_PATH    = MEMORY_DIR / "agent.db"
VECTORS_PATH   = str(MEMORY_DIR / "vectors")


# ==============================
# ПРОВЕРКА ПРИ СТАРТЕ
# ==============================
def validate_config() -> list[str]:
    """
    Проверяет что обязательные ключи заполнены.
    Возвращает список проблем (пустой = всё ок).
    """
    issues = []

    if not ANTHROPIC_API_KEY and not OPENAI_API_KEY:
        issues.append("❌ Не задан ни ANTHROPIC_API_KEY ни OPENAI_API_KEY")

    if not TAVILY_API_KEY:
        issues.append("⚠️  TAVILY_API_KEY не задан — веб-поиск недоступен")

    if not CHECKO_API_KEY:
        issues.append("⚠️  CHECKO_API_KEY не задан — поиск организаций недоступен")

    return issues
