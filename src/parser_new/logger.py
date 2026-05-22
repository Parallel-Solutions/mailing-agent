import sys
from loguru import logger
from src.parser_new.config import LOG_FILE

# Убираем дефолтный обработчик
logger.remove()

# Логи в консоль (для разработки)
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> — {message}",
    level="INFO",
    colorize=True,
)

# Логи в файл (полная история)
logger.add(
    LOG_FILE,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} — {message}",
    level="DEBUG",
    rotation="10 MB",    # новый файл каждые 10 МБ
    retention="30 days", # хранить 30 дней
    encoding="utf-8",
)
