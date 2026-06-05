from __future__ import annotations

import re
from typing import Any, Callable

StatusLoader = Callable[[str | None], dict[str, Any]]
ChatWithOrchestrator = Callable[..., dict[str, Any] | None]


def _documents_agent_recent_event_lines(state: dict, *, limit: int = 3) -> list[str]:
    lines: list[str] = []
    for event in (state.get("recent_events") or [])[:limit]:
        if not isinstance(event, dict):
            continue
        text = str(event.get("message") or event.get("summary") or "").strip()
        if text:
            lines.append(text.rstrip(".") + ".")
    return lines


def _documents_agent_stage_label(documents_status: dict) -> str:
    stage = str(documents_status.get("stage") or "")
    generator = documents_status.get("generator") or {}
    generator_stage = str(generator.get("stage") or "")
    if stage == "review":
        return "проверка текста"
    if generator_stage == "review_templates":
        return "проверка шаблонов"
    if generator_stage == "render_docx":
        return "подготовка документов"
    if generator_stage == "convert_pdf":
        return "сбор результата"
    if generator_stage == "finalize_output":
        return "сбор результата"
    if stage == "completed":
        return "подготовка завершена"
    return "подготовка документов"


def _documents_agent_process_reply(documents_status: dict) -> str:
    status = str(documents_status.get("status") or "idle")
    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    total_rows = int(documents_status.get("total_rows") or 0)
    processed_rows = int(documents_status.get("processed_rows") or 0)
    total_documents = int(documents_status.get("total_documents") or 0)
    reviewed_documents = int(documents_status.get("reviewed_documents") or 0)
    output_file_count = int(documents_status.get("output_file_count") or 0)
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    documents_with_issues = int(documents_status.get("documents_with_issues") or 0)
    error_rows = int(documents_status.get("error_rows") or 0)
    result_done = int(generator.get("pdf_processed") or generator.get("staged_pdf_count") or 0)
    result_total = int(generator.get("pdf_total") or 0)
    stage_label = _documents_agent_stage_label(documents_status)

    if status == "running":
        parts = [f"Сейчас идёт {stage_label}."]
        if documents_status.get("stage") == "review" and total_documents > 0:
            parts.append(f"Проверено {reviewed_documents} из {total_documents} документов.")
        elif documents_status.get("stage") == "ready" and result_total > 0:
            parts.append(f"Собираю результат: {result_done} из {result_total} файлов.")
        elif total_rows > 0:
            parts.append(f"Готово {processed_rows} из {total_rows} клиентов.")
        recent = _documents_agent_recent_event_lines(philologist if documents_status.get("stage") == "review" else generator, limit=2)
        if recent:
            parts.append("Последние события: " + " ".join(recent))
        parts.append("Действие пользователя сейчас не нужно, просто дождитесь завершения.")
        return " ".join(parts)

    if status == "completed":
        parts = ["Подготовка документов завершена."]
        if total_rows > 0:
            parts.append(f"Обработано клиентов: {total_rows}.")
        if output_file_count > 0:
            parts.append(f"Готовых файлов в результате: {output_file_count}.")
        if fixed_documents > 0:
            parts.append(f"Безопасные исправления внесены в {fixed_documents} документах.")
        if documents_with_issues > 0:
            parts.append(f"Замечания остались в {documents_with_issues} документах.")
        parts.append("Можно скачать документы и перейти к проверке отправки.")
        return " ".join(parts)

    if status == "stopped":
        return (
            f"Подготовка документов остановлена на этапе «{stage_label}». "
            "Прогресс сохранён. Можно нажать «Продолжить подготовку» и продолжить с этого места."
        )

    if status == "error":
        summary = str(documents_status.get("summary_text") or "").strip() or "Не удалось завершить подготовку документов."
        parts = [summary]
        if error_rows > 0:
            parts.append(f"Строк с ошибками: {error_rows}.")
        parts.append("Сначала попробуйте повторить запуск. Если ошибка повторится, проверьте таблицу и шаблоны.")
        return " ".join(parts)

    if status == "waiting_review":
        return (
            "Документы уже созданы, но проверка текста ещё не завершена. "
            "Следующий шаг: запустить или дождаться завершения проверки текста."
        )

    return (
        "Подготовка документов ещё не запускалась. "
        "Сначала загрузите таблицу и шаблоны, затем нажмите «Подготовить документы»."
    )


def _documents_agent_duration_reply(documents_status: dict) -> str:
    status = str(documents_status.get("status") or "idle")
    total_documents = int(documents_status.get("total_documents") or 0)
    reviewed_documents = int(documents_status.get("reviewed_documents") or 0)
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    if status == "running" and documents_status.get("stage") == "review" and total_documents > 0:
        remaining = max(0, total_documents - reviewed_documents)
        return (
            "Долго выглядит потому, что сейчас проверяются готовые DOCX по одному: "
            f"проверено {reviewed_documents} из {total_documents}, осталось {remaining}. "
            f"Автоправки уже применены в {fixed_documents} документах. "
            "Это этап проверки текста, отправка ещё не началась."
        )
    return _documents_agent_process_reply(documents_status)


def _documents_agent_result_reply(documents_status: dict) -> str:
    total_rows = int(documents_status.get("total_rows") or 0)
    output_file_count = int(documents_status.get("output_file_count") or 0)
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    documents_with_issues = int(documents_status.get("documents_with_issues") or 0)
    reviewed_documents = int(documents_status.get("reviewed_documents") or 0)
    total_documents = int(documents_status.get("total_documents") or 0)
    return (
        "Сводка по подготовке: "
        f"клиентов обработано {total_rows}, "
        f"готовых файлов {output_file_count}, "
        f"проверено документов {reviewed_documents} из {total_documents}, "
        f"безопасных исправлений {fixed_documents}, "
        f"документов с замечаниями {documents_with_issues}. "
        "Если нужен разбор ошибок по тексту, могу показать его по данным филолога."
    )


def _documents_agent_download_reply(documents_status: dict) -> str:
    status = str(documents_status.get("status") or "idle")
    output_file_count = int(documents_status.get("output_file_count") or 0)
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    if status != "completed":
        return (
            "Архив и итоговый отчёт лучше скачивать после завершения подготовки. "
            "Пока дождитесь статуса «Готово»."
        )
    parts = []
    if output_file_count > 0:
        parts.append("Архив документов уже готов к скачиванию.")
    else:
        parts.append("Подготовка завершена, но архив пока не найден.")
    parts.append(
        "Кнопка «Скачать отчёт по исправлениям» нужна, если хотите посмотреть текстовые правки и замечания."
    )
    if fixed_documents <= 0:
        parts.append("Существенных автоматических правок по тексту не было.")
    return " ".join(parts)


def _documents_agent_next_step_reply(documents_status: dict) -> str:
    status = str(documents_status.get("status") or "idle")
    if status == "completed":
        return "Следующий шаг: скачать архив при необходимости и перейти к проверке отправки писем."
    if status == "running":
        return "Сейчас ничего делать не нужно. Следующий шаг откроется автоматически после завершения подготовки."
    if status == "stopped":
        return "Следующий шаг сейчас недоступен. Сначала продолжите подготовку документов."
    if status == "error":
        return "Сначала нужно повторить подготовку и завершить её без ошибки."
    return "Сначала запустите подготовку документов."


def _documents_agent_text_reply(documents_status: dict) -> str:
    status = str(documents_status.get("status") or "idle")
    total_documents = int(documents_status.get("total_documents") or 0)
    reviewed_documents = int(documents_status.get("reviewed_documents") or 0)
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    documents_with_issues = int(documents_status.get("documents_with_issues") or 0)
    if status == "running" and documents_status.get("stage") == "review":
        return (
            "Я сейчас проверяю текст в готовых документах. "
            f"Проверено {reviewed_documents} из {total_documents}, "
            f"безопасные правки внесены в {fixed_documents}. "
            "Подробный журнал не показываю в чате, чтобы не засорять экран; итоговый отчёт будет доступен после завершения."
        )
    if status == "completed":
        if fixed_documents > 0 or documents_with_issues > 0:
            return (
                "Тексты уже проверены. "
                f"Автоправки внесены в {fixed_documents} документах, "
                f"документов с замечаниями: {documents_with_issues}. "
                "Детали можно посмотреть в отчёте по исправлениям."
            )
        return "Тексты уже проверены, существенных автоматических правок не потребовалось."
    return _documents_agent_process_reply(documents_status)


def _documents_agent_general_reply(message: str, documents_status: dict | None = None) -> str:
    lowered = message.lower()
    status = str((documents_status or {}).get("status") or "idle")

    if any(token in lowered for token in ("привет", "здравств", "добрый", "хай", "hello")):
        if status == "running":
            return "Привет. Я на связи и слежу за подготовкой документов. Статистику не буду сыпать без запроса."
        return "Привет. Я здесь, помогу с документами: могу подсказать статус, ошибки или следующий шаг."

    if any(token in lowered for token in ("спасибо", "поняла", "понял", "окей", "ок", "хорошо")):
        return "Окей, я рядом. Если нужен статус или ошибки, спросите прямо, и я отвечу коротко."

    if any(token in lowered for token in ("ты агент", "ты вообще агент", "ты завис", "завис", "отвечаешь")):
        return (
            "Да, я отвечаю как помощник по экрану документов. "
            "Буду писать коротко и по делу, без технических журналов и лишней статистики."
        )

    if any(token in lowered for token in ("непонят", "что за", "почему так", "странно", "жесть")):
        return (
            "Понимаю. Я не буду выгружать техническую простыню в чат. "
            "Если хотите, могу отдельно коротко объяснить статус, ошибку или следующий шаг."
        )

    if status == "running":
        return "Я на связи. Подготовка идёт, но подробные цифры покажу только если спросите про статус."
    if status == "completed":
        return "Я на связи. Подготовка завершена; могу подсказать, что скачать или куда идти дальше."
    if status == "error":
        return "Я на связи. Если нужно, коротко разберу ошибку и следующий безопасный шаг."
    return "Я на связи. Могу помочь со статусом подготовки, ошибками, скачиванием документов или следующим шагом."


def _documents_agent_tool_get_documents_status(job_id: str | None, status_loader: StatusLoader) -> dict:
    return status_loader(job_id)


def _documents_agent_tool_get_current_step(job_id: str | None, status_loader: StatusLoader) -> tuple[str, dict]:
    documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
    return _documents_agent_process_reply(documents_status), documents_status


def _documents_agent_tool_get_errors(job_id: str | None, status_loader: StatusLoader) -> tuple[str, dict]:
    documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
    status = str(documents_status.get("status") or "idle")
    error_rows = int(documents_status.get("error_rows") or 0)
    documents_with_issues = int(documents_status.get("documents_with_issues") or 0)
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    summary = str(documents_status.get("summary_text") or "").strip()

    if status == "error":
        details = summary or "Подготовка документов завершилась с ошибкой."
        if error_rows > 0:
            details += f" Строк с ошибками: {error_rows}."
        return f"{details} Можно повторить запуск после проверки таблицы и шаблонов.", documents_status
    if documents_with_issues > 0:
        return (
            f"Критической ошибки процесса нет. После проверки текста остались замечания в {documents_with_issues} документах. "
            "Их лучше смотреть в отчёте по исправлениям."
        ), documents_status
    if fixed_documents > 0:
        return f"Критической ошибки процесса нет. Безопасные правки внесены в {fixed_documents} документах.", documents_status
    return "Сейчас явных ошибок по подготовке документов не вижу.", documents_status


def _documents_agent_tool_get_downloads(job_id: str | None, status_loader: StatusLoader) -> tuple[str, dict]:
    documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
    return _documents_agent_download_reply(documents_status), documents_status


def _documents_agent_tool_get_text_review_summary(job_id: str | None, status_loader: StatusLoader) -> tuple[str, dict]:
    documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
    return _documents_agent_text_reply(documents_status), documents_status


def _documents_agent_tool_get_technical_log(job_id: str | None, status_loader: StatusLoader) -> tuple[str, dict]:
    documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    events = _documents_agent_recent_event_lines(generator, limit=2)
    events.extend(_documents_agent_recent_event_lines(philologist, limit=2))
    if not events:
        return "Технических событий для показа сейчас нет. В обычном чате я держу только короткие человеческие сообщения.", documents_status
    return (
        "Короткий технический хвост без JSON: "
        + " ".join(events[:4])
        + " Полный служебный журнал в чат не вывожу, чтобы не раздувать экран."
    ), documents_status


def _documents_agent_scroll_test_reply() -> str:
    lines = [
        "Тест длинного сообщения для проверки скролла чата.",
        "Если всё работает правильно, это сообщение не должно растянуть весь экран.",
        "У блока чата должен появиться собственный бегунок, а длинный пузырь должен скроллиться внутри себя.",
        "",
    ]
    for index in range(1, 41):
        lines.append(
            f"Строка {index:02d}: проверяю, что длинный текст остаётся внутри чат-панели "
            "и не переносит прокрутку на всю страницу."
        )
    lines.append("")
    lines.append("Конец теста. После этой строки можно проверить, где находится бегунок.")
    return "\n".join(lines)


def _documents_agent_should_delegate_to_philologist(message: str, documents_status: dict | None = None) -> bool:
    lowered = message.lower()
    philologist_keywords = (
        "филолог", "ошиб", "исправ", "правк", "грамот", "граммат", "орфограф",
        "пунктуа", "текст", "формулиров", "замечан", "правило", "документе"
    )
    if any(token in lowered for token in philologist_keywords):
        return True
    return int((documents_status or {}).get("reviewed_documents") or 0) > 0 and any(
        token in lowered for token in ("что не так", "что исправ", "какие проблемы", "какие замечания")
    )


def _safe_agent_reply(reply: Any) -> str:
    text = re.sub(r"\s+", " ", str(reply or "")).strip()
    if not text:
        return "Я на связи. Могу подсказать статус подготовки документов или следующий шаг."
    lower = text.lower()
    noisy_markers = (
        "журнал склонений",
        "всего проверок",
        "applied current local rule",
        "legacy_rule",
        "agent_loop",
        "tool_trace",
        "read_inflection_log",
        "review_docx",
        "контекст:",
    )
    if len(text) > 900 or any(marker in lower for marker in noisy_markers):
        return (
            "Вижу внутренний технический журнал, но в чат его выводить не буду. "
            "Коротко: процесс идёт, детали будут в итоговом отчёте по исправлениям."
        )
    return text


def _documents_agent_reply_payload(
    reply: str,
    *,
    source: str = "documents_agent",
    allow_long_reply: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reply": str(reply or "") if allow_long_reply else _safe_agent_reply(reply),
        "source": source,
    }
    payload.update(extra)
    return payload


def _documents_agent_ai_reply(
    message: str,
    documents_status: dict,
    job_id: str | None,
    chat_with_orchestrator: ChatWithOrchestrator | None,
) -> dict[str, Any] | None:
    if not callable(chat_with_orchestrator):
        return None

    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    context = {
        "screen": "documents",
        "job_id": job_id or "",
        "status": documents_status.get("status"),
        "stage": documents_status.get("stage"),
        "stage_text": documents_status.get("stage_text"),
        "current_item_text": documents_status.get("current_item_text"),
        "progress_percent": documents_status.get("progress_percent"),
        "total_rows": documents_status.get("total_rows"),
        "processed_rows": documents_status.get("processed_rows"),
        "total_documents": documents_status.get("total_documents"),
        "reviewed_documents": documents_status.get("reviewed_documents"),
        "error_rows": documents_status.get("error_rows"),
        "fixed_documents": documents_status.get("fixed_documents"),
        "documents_with_issues": documents_status.get("documents_with_issues"),
        "generator_status": generator.get("status"),
        "generator_stage": generator.get("stage"),
        "philologist_status": philologist.get("status"),
    }
    agent_message = (
        "Ты отвечаешь в чате экрана «Документы» сервиса рассылки. "
        "Ты единый дружелюбный агент интерфейса, а не технический логгер. "
        "Отвечай пользователю коротко, по-русски и по делу. "
        "Используй контекст текущей job-сессии ниже как главный источник правды. "
        "Не выводи служебные журналы, JSON, названия инструментов, stack trace, trace, reason-логи и внутренние поля. "
        "Если в контексте есть технические детали, перескажи их человечески в 1-3 предложениях. "
        "Не говори пользователю, что он должен ничего не нажимать, если это не нужно для ответа. "
        "Не запускай тяжёлые действия и инструменты, если пользователь прямо не просит запуск. "
        "Если пользователь просто здоровается, ответь живо и предложи помочь со статусом документов.\n\n"
        f"Контекст экрана документов:\n{context}\n\n"
        f"Сообщение пользователя: {message}"
    )
    session_id = f"documents:{job_id or 'default'}"
    try:
        result = chat_with_orchestrator(agent_message, session_id=session_id)
    except Exception:
        return None
    reply = str((result or {}).get("reply") or "").strip()
    if not reply:
        return None
    return _documents_agent_reply_payload(
        reply,
        source="orchestrator",
        session_id=str((result or {}).get("session_id") or session_id),
        downloads=(result or {}).get("downloads") or [],
    )


def choose_documents_agent_reply(
    message: str,
    job_id: str | None = None,
    *,
    status_loader: StatusLoader,
    chat_with_orchestrator: ChatWithOrchestrator | None = None,
) -> dict[str, Any]:
    lowered = message.lower()

    if any(token in lowered for token in ("/test-scroll", "тест бегунка", "тест скролла", "проверить бегунок")):
        return _documents_agent_reply_payload(
            _documents_agent_scroll_test_reply(),
            source="debug_scroll_test",
            allow_long_reply=True,
            tools_used=[],
        )

    if any(token in lowered for token in ("привет", "здравств", "добрый", "хай", "hello", "спасибо", "поняла", "понял", "окей", "ок", "хорошо")):
        return _documents_agent_reply_payload(_documents_agent_general_reply(message), tools_used=[])
    if any(token in lowered for token in ("ты агент", "ты вообще агент", "ты завис", "завис", "отвечаешь", "непонят", "что за", "почему так", "странно", "жесть")):
        if not any(token in lowered for token in ("почему долго", "так долго", "статус", "что происходит", "ошиб", "сколько")):
            return _documents_agent_reply_payload(_documents_agent_general_reply(message), tools_used=[])

    if any(token in lowered for token in ("технический лог", "служебный лог", "полный лог", "trace", "tool_trace", "журнал агента")):
        reply, _ = _documents_agent_tool_get_technical_log(job_id, status_loader)
        return _documents_agent_reply_payload(reply, tools_used=["get_technical_log"])

    if any(token in lowered for token in ("почему долго", "так долго", "долго провер", "медленно", "тормоз")):
        documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
        return _documents_agent_reply_payload(
            _documents_agent_duration_reply(documents_status),
            tools_used=["get_documents_status"],
        )
    if any(token in lowered for token in ("что происходит", "на каком этапе", "статус", "идет ли", "идёт ли", "что сейчас", "процесс")):
        reply, _ = _documents_agent_tool_get_current_step(job_id, status_loader)
        return _documents_agent_reply_payload(reply, tools_used=["get_current_step"])
    if any(token in lowered for token in ("что дальше", "следующ", "можно ли дальше", "переход", "кнопк", "актив", "доступ", "заблок")):
        documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
        return _documents_agent_reply_payload(
            _documents_agent_next_step_reply(documents_status),
            tools_used=["get_documents_status", "get_current_step"],
        )
    if any(token in lowered for token in ("скачать", "архив", "отч", "файл", "pdf", "docx")):
        reply, _ = _documents_agent_tool_get_downloads(job_id, status_loader)
        return _documents_agent_reply_payload(reply, tools_used=["get_downloads"])
    if any(token in lowered for token in ("итог", "результат", "сколько готово", "сколько документов", "сводк")):
        documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
        return _documents_agent_reply_payload(
            _documents_agent_result_reply(documents_status),
            tools_used=["get_documents_status"],
        )

    if any(token in lowered for token in ("ошиб", "проблем", "упал", "не работает", "сломал")):
        reply, _ = _documents_agent_tool_get_errors(job_id, status_loader)
        return _documents_agent_reply_payload(reply, tools_used=["get_errors"])

    if _documents_agent_should_delegate_to_philologist(message):
        reply, _ = _documents_agent_tool_get_text_review_summary(job_id, status_loader)
        return _documents_agent_reply_payload(reply, tools_used=["get_text_review_summary"])

    return _documents_agent_reply_payload(_documents_agent_general_reply(message), tools_used=[])
