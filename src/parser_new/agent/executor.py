"""
agent/executor.py
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage

from src.parser_new.agent.agent import build_agent, format_chat_history
from src.parser_new.memory.memory_manager import init_memory
from src.parser_new.logger import logger


@dataclass
class LogEntry:
    type: str
    text: str


@dataclass
class AgentResult:
    success: bool
    text: str
    log: list[LogEntry] = field(default_factory=list)
    file_path: str | None = None


def _parse_messages(messages: list) -> list[LogEntry]:
    entries = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                entries.append(LogEntry(
                    type="tool",
                    text=f"Инструмент: {tc['name']} | {str(tc.get('args', ''))[:120]}"
                ))
        elif hasattr(msg, "name") and msg.name:
            preview = str(getattr(msg, "content", ""))[:200]
            t = "error" if "error" in preview.lower() else "ok"
            entries.append(LogEntry(type=t, text=f"{msg.name}: {preview}"))
    return entries


init_memory()

_agent_executor = None


def get_agent():
    global _agent_executor
    if _agent_executor is None:
        logger.info("Инициализация агента...")
        _agent_executor = build_agent()
    return _agent_executor


def _run_single_batch(task: str, chat_history: list[dict]) -> tuple[str, list[LogEntry]]:
    """Запускает одну задачу агента. Возвращает (текст_ответа, лог)."""
    log = []
    history = format_chat_history(chat_history)
    messages = history + [HumanMessage(content=task)]
    result = get_agent().invoke({"messages": messages})
    all_messages = result.get("messages", [])
    log.extend(_parse_messages(all_messages))
    ai_messages = [m for m in all_messages if isinstance(m, AIMessage)]
    output_text = ai_messages[-1].content if ai_messages else ""
    return output_text, log


def _detect_mo_list(task: str) -> list[str] | None:
    import re
    pattern = r"(?:Республика|край|область|округ)[^,]+" \
              r"(?:район)[^,]+" \
              r"(?:поселени|сельсовет|посёлок|поселок|село)[^,]+"
    matches = re.findall(pattern, task, re.IGNORECASE)
    if len(matches) > 5:
        return [m.strip() for m in matches]
    return None


def run_agent_task(
    task: str,
    chat_history: list[dict],
    uploaded_file_path: str | None = None,
    mode: str = "С уточнениями",
) -> AgentResult:
    log: list[LogEntry] = []
    log.append(LogEntry("step", "Задание получено"))

    try:
        full_task = _build_task_text(task, uploaded_file_path, mode)
        log.append(LogEntry("think", "Анализирую задание..."))

        # Определяем — есть ли длинный список МО
        mo_list = _detect_mo_list(task)

        if mo_list and len(mo_list) > 5:
            # Разбиваем на пачки по 5 и обрабатываем отдельно
            BATCH_SIZE = 5
            batches = [mo_list[i:i+BATCH_SIZE] for i in range(0, len(mo_list), BATCH_SIZE)]
            log.append(LogEntry("step", f"Найдено {len(mo_list)} МО — разбиваю на {len(batches)} пачки по {BATCH_SIZE}"))

            all_outputs = []
            for i, batch in enumerate(batches):
                log.append(LogEntry("step", f"Обрабатываю пачку {i+1}/{len(batches)}: {len(batch)} МО"))

                # Извлекаем суть задания (без списка МО) и добавляем только текущую пачку
                batch_text = "\n".join(batch)
                # Берём инструкцию из исходного задания (всё до первого МО)
                import re
                instruction_match = re.split(
                    r'Республика|Забайкальский|Московская|Ленинградская|Свердловская',
                    full_task, maxsplit=1
                )
                instruction = instruction_match[0].strip() if instruction_match else full_task

                batch_task = f"{instruction}\n\n{batch_text}"
                if uploaded_file_path:
                    batch_task += f"\n\nФайл: {uploaded_file_path}"

                try:
                    output, batch_log = _run_single_batch(batch_task, chat_history)
                    log.extend(batch_log)
                    all_outputs.append(output)
                    log.append(LogEntry("ok", f"Пачка {i+1} готова"))
                except Exception as e:
                    log.append(LogEntry("error", f"Ошибка в пачке {i+1}: {str(e)[:100]}"))
                    all_outputs.append(f"Ошибка в пачке {i+1}: {e}")

            output_text = "\n\n".join(filter(None, all_outputs))
            log.append(LogEntry("ok", f"Все {len(batches)} пачки обработаны"))

        else:
            # Обычный режим — одна задача
            output_text, task_log = _run_single_batch(full_task, chat_history)
            log.extend(task_log)
            log.append(LogEntry("ok", "Задача выполнена"))

        file_path = _find_result_file()
        return AgentResult(success=True, text=output_text, log=log, file_path=file_path)

    except Exception as e:
        log.append(LogEntry("error", f"Ошибка: {str(e)[:200]}"))
        logger.error(f"Ошибка агента: {e}")
        logger.error(traceback.format_exc())
        return AgentResult(success=False, text=f"Ошибка: {e}", log=log)


def _build_task_text(task, file_path, mode):
    parts = [task]
    if file_path:
        parts.append(
            f"\n\nК заданию приложен файл: {file_path}"
            "\nЭто файл со строками для пакетной обработки. НЕ оценивай, "
            "заполнены ли строки, и НЕ полагайся на read_excel_tool для этого решения. "
            "Если пользователь просит найти/дозаполнить данные (реквизиты, контакты, главу, "
            "ИНН и т.п.) — СРАЗУ вызови batch_search_tool с параметром file_path выше. "
            "Скрипт сам определит, какие строки и поля нужно заполнить, в том числе по "
            "муниципальным районам (когда колонка МО пустая, а район заполнен). "
            "Если пользователь просит обновить почты — вызови fix_emails_tool с этим файлом."
            "\n\nОСОБЫЙ СЛУЧАЙ: если пользователь просит СОБРАТЬ СПИСОК округов или "
            "МО из классификатора (например «найди все муниципальные округа России», "
            "«собери все МО региона») — это НЕ про приложенный файл. Используй "
            "build_all_okrugs_file_tool (вся Россия) или build_region_mo_file_tool "
            "(один регион), а приложенный файл игнорируй."
        )
    if mode == "Автоматический":
        parts.append("\n\nРежим: Автоматический. Не задавай уточняющих вопросов.")
    else:
        parts.append("\n\nРежим: С уточнениями. Если задача неоднозначна - спроси.")
    return "".join(parts)


def _find_result_file():
    from config import OUTPUT_DIR
    files = sorted(
        (OUTPUT_DIR / "latest").glob("*.xlsx"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return str(files[0]) if files else None


def save_uploaded_file(uploaded_file) -> str:
    from config import OUTPUT_DIR

    upload_dir = OUTPUT_DIR.parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Используем простое имя без спецсимволов
    suffix = Path(uploaded_file.name).suffix
    file_path = upload_dir / f"upload{suffix}"

    # getbuffer() — правильный способ читать UploadedFile от Streamlit
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    logger.info(f"Файл сохранён: {file_path}")
    return str(file_path)