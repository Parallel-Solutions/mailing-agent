"""
tools/batch_tool.py — инструмент пакетной обработки для агента.

Запускает batch_processor на загруженном файле и возвращает результат.
"""
from __future__ import annotations

from langchain.tools import tool

try:
    from src.parser_new.logger import logger
except ImportError:
    from logger import logger


@tool
def batch_search_tool(file_path: str, save_every: int = 10) -> str:
    """
    Пакетная обработка файла Excel — находит данные по всем незаполненным МО.
    Используй когда пользователь прислал файл и просит дозаполнить строки
    или найти данные по списку МО.

    Скрипт автоматически:
    1. Находит незаполненные строки
    2. Ищет администрацию каждого МО через Яндекс → rusprofile → Checko
    3. Заполняет реквизиты, контакты, главу, ОКТМО
    4. Для ликвидированных пишет "ликвидирована" в ADM_NAME
    5. Сохраняет результат и файл с непроверенными строками

    Параметры:
      file_path:  путь к Excel файлу
      save_every: сохранять прогресс каждые N строк (по умолчанию 10)

    Возвращает краткий отчёт о результатах.
    """
    try:
        try:
            from src.parser_new.batch_processor import run
        except ImportError:
            from batch_processor import run

        logger.info(f"[batch_tool] Запуск пакетной обработки: {file_path}")
        result = run(file_path=file_path, save_every=save_every)

        if not result:
            return "Ошибка: batch_processor не вернул результат"

        if result.get("error"):
            return f"Ошибка: {result['error']}"

        processed = result.get("processed", 0)
        found = result.get("found", 0)
        not_found = result.get("not_found", 0)
        liquidated = result.get("liquidated", 0)
        output_path = result.get("output_path", "")
        failed_path = result.get("failed_path", "")

        lines = [
            f"Пакетная обработка завершена:",
            f"  Обработано: {processed}",
            f"  Найдено: {found}",
            f"  Не найдено: {not_found}",
            f"  Ликвидировано: {liquidated}",
            f"  Файл результата: {output_path}",
        ]
        if not_found > 0:
            lines.append(f"  Файл непроверенных: {failed_path}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[batch_tool] Ошибка: {e}")
        return f"Ошибка пакетной обработки: {e}"