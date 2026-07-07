from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.generator.inflection.ai_case_agent import (
    OpenAI,
    _build_openai_http_client,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.generator.knowledge.service_knowledge import find_relevant_service_docs, format_service_rag_context
from src.jobs import load_agent_state, resolve_job_paths, resolve_state_path
from src.jobs.job_docs import read_events, read_sent_mail_log
from src.utils.config import settings

StatusLoader = Callable[[str | None], dict[str, Any]]
ChatWithOrchestrator = Callable[..., dict[str, Any] | None]


_WORD_CHARS_RE = r"A-Za-zА-Яа-яЁё0-9_"


def _documents_agent_has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![{_WORD_CHARS_RE}]){re.escape(word)}(?![{_WORD_CHARS_RE}])", text, flags=re.IGNORECASE))


def _documents_agent_is_ack_or_greeting(text: str) -> bool:
    return any(
        token in text
        for token in ("привет", "здравств", "добрый", "хай", "hello", "спасибо", "поняла", "понял", "окей", "хорошо")
    ) or _documents_agent_has_word(text, "ок")


def _documents_agent_is_capabilities_question(text: str) -> bool:
    return any(
        token in text
        for token in (
            "что ты умеешь",
            "что умеешь",
            "чем можешь",
            "что можешь",
            "как ты можешь",
            "какие вопросы",
            "возможност",
        )
    )


def _documents_agent_capabilities_reply(documents_status: dict | None = None) -> str:
    status = str((documents_status or {}).get("status") or "idle")
    status_hint = {
        "completed": "Сейчас подготовка уже завершена, поэтому могу сразу подсказать, что скачивать и что проверять перед отправкой.",
        "running": "Сейчас подготовка идёт, поэтому могу коротко объяснять текущий этап и последние безопасные события.",
        "error": "Сейчас есть ошибка, поэтому могу перевести её на нормальный язык и подсказать следующий шаг.",
        "stopped": "Сейчас процесс остановлен, поэтому могу объяснить, где остановились и как продолжить.",
    }.get(status, "Сейчас подготовка не активна, поэтому могу помочь с запуском и проверкой исходных данных.")
    return (
        "Я могу ответить по этому экрану: что сейчас с документами, есть ли ошибки, какие файлы готовы, "
        "что делать дальше, что можно скачать и что текстовая проверка исправила или оставила на ручную проверку. "
        "Я ничего сам не запускаю и не отправляю, только объясняю состояние по данным текущей сессии. "
        + status_hint
    )


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
    output_ready = bool(documents_status.get("output_ready"))
    fixed_documents = int(documents_status.get("fixed_documents") or 0)
    if status != "completed" or not output_ready:
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
    output_ready = bool(documents_status.get("output_ready"))
    if status == "completed" and output_ready:
        return "Следующий шаг: скачать архив при необходимости и перейти к проверке отправки писем."
    if status == "completed":
        return "Архив ещё собирается. Дождитесь завершения подготовки, затем можно будет скачать документы."
    if status == "running":
        return "Сейчас ничего делать не нужно. Следующий шаг откроется автоматически после завершения подготовки."
    if status == "stopped":
        return "Следующий шаг сейчас недоступен. Сначала продолжите подготовку документов."
    if status == "error":
        return "Сначала нужно повторить подготовку и завершить её без ошибки."
    return "Сначала запустите подготовку документов."


def _documents_agent_load_philologist_state(job_id: str | None, documents_status: dict | None = None) -> dict[str, Any]:
    state: dict[str, Any] = {}
    inline = (documents_status or {}).get("philologist")
    if isinstance(inline, dict):
        state.update(inline)
    if not job_id:
        return state
    loaded = load_agent_state("philologist", {}, job_id=job_id, include_details=True)
    if isinstance(loaded, dict):
        state.update(loaded)
    return state


def _documents_agent_document_name(item: dict[str, Any]) -> str:
    value = item.get("name") or item.get("path") or item.get("document") or "документ"
    name = Path(str(value)).name
    return _documents_agent_sanitize_text(name, limit=90) or "документ"


def _documents_agent_fix_examples(philologist_state: dict[str, Any], *, limit: int = 3) -> list[str]:
    examples: list[str] = []
    for item in philologist_state.get("documents") or []:
        if not isinstance(item, dict):
            continue
        document_name = _documents_agent_document_name(item)
        for fix in item.get("applied_fixes") or []:
            if not isinstance(fix, dict):
                continue
            issue = _documents_agent_sanitize_text(fix.get("issue") or fix.get("fragment") or "языковая правка", limit=120)
            suggestion = _documents_agent_sanitize_text(fix.get("suggestion"), limit=120)
            examples.append(f"{document_name}: {issue} -> {suggestion}" if suggestion else f"{document_name}: {issue}")
            if len(examples) >= limit:
                return examples

    corrections = (philologist_state.get("inflection_context_corrections") or {}).get("corrections") or []
    for item in corrections:
        if not isinstance(item, dict):
            continue
        document_name = _documents_agent_sanitize_text(item.get("document") or item.get("field") or "подстановка", limit=90)
        before = _documents_agent_sanitize_text(item.get("generated_value") or item.get("fragment"), limit=90)
        after = _documents_agent_sanitize_text(item.get("corrected_value") or item.get("suggestion"), limit=90)
        if before and after:
            examples.append(f"{document_name}: {before} -> {after}")
        elif after:
            examples.append(f"{document_name}: исправлено на «{after}»")
        if len(examples) >= limit:
            return examples
    return examples


def _documents_agent_issue_examples(philologist_state: dict[str, Any], *, limit: int = 2) -> list[str]:
    examples: list[str] = []
    for item in philologist_state.get("documents") or []:
        if not isinstance(item, dict):
            continue
        document_name = _documents_agent_document_name(item)
        for fix in item.get("skipped_fixes") or []:
            if not isinstance(fix, dict):
                continue
            issue = _documents_agent_sanitize_text(fix.get("issue") or "нужна ручная проверка", limit=120)
            reason = _documents_agent_sanitize_text(fix.get("reason"), limit=100)
            examples.append(f"{document_name}: {issue}" + (f" ({reason})" if reason else ""))
            if len(examples) >= limit:
                return examples
        for issue in item.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            issue_text = _documents_agent_sanitize_text(issue.get("issue") or issue.get("message") or "есть замечание", limit=120)
            examples.append(f"{document_name}: {issue_text}")
            if len(examples) >= limit:
                return examples
    return examples


def _documents_agent_text_reply(documents_status: dict, job_id: str | None = None) -> str:
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
        philologist_state = _documents_agent_load_philologist_state(job_id, documents_status)
        fix_examples = _documents_agent_fix_examples(philologist_state, limit=3)
        issue_examples = _documents_agent_issue_examples(philologist_state, limit=2)
        if fixed_documents > 0 or documents_with_issues > 0:
            parts = [
                "Тексты уже проверены. "
                f"Автоправки внесены в {fixed_documents} документах, "
                f"документов с замечаниями: {documents_with_issues}."
            ]
            if fix_examples:
                parts.append("Примеры исправлений: " + "; ".join(fix_examples) + ".")
            else:
                parts.append("Точных примеров в компактном статусе нет, но полный список есть в отчёте по исправлениям.")
            if issue_examples:
                parts.append("Что осталось проверить вручную: " + "; ".join(issue_examples) + ".")
            parts.append("Для полного списка скачайте отчёт по исправлениям.")
            return " ".join(parts)
        return "Тексты уже проверены, существенных автоматических правок не потребовалось."
    return _documents_agent_process_reply(documents_status)


def _documents_agent_general_reply(message: str, documents_status: dict | None = None) -> str:
    lowered = message.lower()
    status = str((documents_status or {}).get("status") or "idle")

    if any(token in lowered for token in ("привет", "здравств", "добрый", "хай", "hello")):
        if status == "running":
            return "Привет. Я на связи и слежу за подготовкой документов. Статистику не буду сыпать без запроса."
        return "Привет. Я здесь, помогу с документами: могу подсказать статус, ошибки или следующий шаг."

    if any(token in lowered for token in ("спасибо", "поняла", "понял", "окей", "хорошо")) or _documents_agent_has_word(lowered, "ок"):
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


def _documents_agent_ai_unavailable_reply() -> str:
    return (
        "Я не смог получить ответ от AI-помощника. "
        "Могу отвечать только на встроенные вопросы по статусу: что сейчас происходит, есть ли ошибки, "
        "что скачать, какие файлы готовы, какие исправления найдены и какой следующий шаг."
    )

def _documents_agent_rag_reply(message: str, documents_status: dict | None = None) -> str | None:
    lowered = message.lower()
    knowledge_intent = any(
        token in lowered
        for token in (
            "как работает",
            "как устро",
            "что такое",
            "зачем",
            "rag",
            "чат",
            "шаблон",
            "gotenberg",
            "профилиров",
        )
    )
    if not knowledge_intent:
        return None
    docs = find_relevant_service_docs(message, limit=2)
    if not docs:
        return None

    status = str((documents_status or {}).get("status") or "")
    stage_text = str((documents_status or {}).get("stage_text") or "").strip()
    answer = str(docs[0].get("answer") or "").strip()
    if not answer:
        return None
    if status and stage_text:
        answer += f" По текущей сессии сейчас: {stage_text}"
    return answer


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
    return _documents_agent_text_reply(documents_status, job_id), documents_status


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



_MAX_DIAGNOSTIC_TEXT = 16000
_MAX_LOG_LINE_LENGTH = 500
_SECRET_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
    (re.compile(r"rs_ck_v1_[A-Za-z0-9_\-=]{12,}"), "rs_ck_v1_***"),
    (re.compile(r"eyJ[A-Za-z0-9_\-.]{40,}"), "jwt-***"),
    (re.compile(r"(?i)(Bearer\s+)[^\s\"']+"), r"\1***"),
    (re.compile(r"(?i)((?:api[_-]?key|token|password|secret)[\w\-]*[\s:=]+)[^,\s\"'}]+"), r"\1***"),
)


def _documents_agent_sanitize_text(value: Any, *, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _documents_agent_safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _documents_agent_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"_read_error": f"{type(exc).__name__}: {exc}"}
    return payload if isinstance(payload, dict) else {"_invalid_shape": type(payload).__name__}


def _documents_agent_read_jsonl_tail(path: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-max(limit * 4, limit):]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items[-limit:]


def _documents_agent_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        return ""


def _documents_agent_file_info(path: Path) -> dict[str, Any]:
    exists = path.exists()
    info: dict[str, Any] = {"exists": exists}
    if exists:
        try:
            stat = path.stat()
            info.update({"size_bytes": stat.st_size, "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
        except Exception:
            pass
    return info


def _documents_agent_list_files(root: Path, *, limit: int = 20) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "file_count": 0, "total_bytes": 0, "sample": []}
    files: list[tuple[float, Path, int]] = []
    total_bytes = 0
    try:
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            files.append((stat.st_mtime, item, stat.st_size))
    except Exception as exc:
        return {"exists": True, "error": f"{type(exc).__name__}: {exc}", "file_count": 0, "total_bytes": 0, "sample": []}
    files.sort(reverse=True)
    sample = []
    for _, item, size in files[:limit]:
        try:
            name = str(item.relative_to(root))
        except ValueError:
            name = item.name
        sample.append({"name": _documents_agent_sanitize_text(name, limit=180), "size_bytes": size})
    return {"exists": True, "file_count": len(files), "total_bytes": total_bytes, "sample": sample}


def _documents_agent_tail_log(path: Path, *, limit: int = 10) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    clean = [_documents_agent_sanitize_text(line, limit=_MAX_LOG_LINE_LENGTH) for line in lines if line.strip()]
    return clean[-limit:]


def _documents_agent_worker_diagnostics(state_dir: Path) -> dict[str, Any]:
    if not state_dir.exists():
        return {"state_dir_exists": False, "statuses": [], "logs": []}
    status_paths = sorted(state_dir.glob("worker-*.status.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0)[-5:]
    statuses = []
    for path in status_paths:
        payload = _documents_agent_read_json(path)
        statuses.append({
            "file": path.name,
            "task": payload.get("task"),
            "status": payload.get("status"),
            "return_code": payload.get("return_code"),
            "message": _documents_agent_sanitize_text(payload.get("message"), limit=300),
            "started_at": payload.get("started_at"),
            "updated_at": payload.get("updated_at"),
            "completed_at": payload.get("completed_at"),
        })
    log_paths = sorted(
        list(state_dir.glob("worker-*.err.log")) + list(state_dir.glob("worker-*.out.log")),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
    )[-4:]
    logs = []
    for path in log_paths:
        tail = _documents_agent_tail_log(path, limit=8)
        if tail:
            logs.append({"file": path.name, "tail": tail})
    return {"state_dir_exists": True, "statuses": statuses, "logs": logs}


def _documents_agent_generator_details(details: dict[str, Any]) -> dict[str, Any]:
    results = details.get("results") if isinstance(details, dict) else None
    compact_results = []
    if isinstance(results, list):
        for item in results[:5]:
            if not isinstance(item, dict):
                continue
            files = item.get("files") if isinstance(item.get("files"), dict) else {}
            generated_files = item.get("generated_files") if isinstance(item.get("generated_files"), dict) else {}
            compact_results.append({
                "id": item.get("id"),
                "row_index": item.get("row_index"),
                "status": item.get("status"),
                "error": _documents_agent_sanitize_text(item.get("error"), limit=350),
                "warning": _documents_agent_sanitize_text(item.get("warning"), limit=250),
                "files": sorted(str(key) for key in files.keys()),
                "generated_files": sorted(str(key) for key in generated_files.keys()),
            })
    return {
        "results_count": len(results) if isinstance(results, list) else 0,
        "results_sample": compact_results,
        "tasks_count": len(details.get("tasks") or []) if isinstance(details, dict) else 0,
        "recent_events_count": len(details.get("recent_events") or []) if isinstance(details, dict) else 0,
    }


def _documents_agent_sender_context(job_id: str | None) -> dict[str, Any]:
    sender = load_agent_state("sender", {}, job_id=job_id, include_details=True)
    rows = sender.get("rows") if isinstance(sender, dict) else None
    row_sample = []
    if isinstance(rows, list):
        for item in rows[:5]:
            if not isinstance(item, dict):
                continue
            attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
            row_sample.append({
                "id": item.get("id"),
                "mun_name": _documents_agent_sanitize_text(item.get("mun_name"), limit=120),
                "recipient": _documents_agent_sanitize_text(item.get("recipient"), limit=120),
                "result": item.get("result"),
                "error": _documents_agent_sanitize_text(item.get("error"), limit=300),
                "attempts": [
                    {
                        "recipient": _documents_agent_sanitize_text(attempt.get("recipient"), limit=120),
                        "status": attempt.get("status"),
                        "error": _documents_agent_sanitize_text(attempt.get("error"), limit=200),
                    }
                    for attempt in attempts[:4]
                    if isinstance(attempt, dict)
                ],
            })
    sent_items = read_sent_mail_log(job_id)
    sent_log_count = len(sent_items)
    return {
        "status": sender.get("status") if isinstance(sender, dict) else None,
        "mode": sender.get("mode") if isinstance(sender, dict) else None,
        "send_mode": sender.get("send_mode") if isinstance(sender, dict) else None,
        "summary_text": _documents_agent_sanitize_text(sender.get("summary_text") if isinstance(sender, dict) else "", limit=350),
        "sent_rows": sender.get("sent_rows") if isinstance(sender, dict) else None,
        "error_rows": sender.get("error_rows") if isinstance(sender, dict) else None,
        "rows_count": len(rows) if isinstance(rows, list) else 0,
        "rows_sample": row_sample,
        "sent_log": {"exists": sent_log_count > 0, "line_count": sent_log_count, "updated_at": None},
    }


def _documents_agent_rusender_context(job_id: str | None) -> dict[str, Any]:
    events = read_events(job_id, "rusender_events")[-50:]
    counts: dict[str, int] = {}
    for event in events:
        code = str(event.get("event") or event.get("type") or event.get("status") or event.get("event_type") or "unknown")
        counts[code] = counts.get(code, 0) + 1
    tail = []
    for event in events[-5:]:
        tail.append({
            "event": event.get("event") or event.get("type") or event.get("event_type"),
            "status": event.get("status"),
            "email": _documents_agent_sanitize_text(event.get("email") or event.get("recipient") or event.get("to"), limit=120),
            "occurred_at": event.get("occurred_at") or event.get("created_at") or event.get("date"),
        })
    return {"recent_count": len(events), "recent_counts": counts, "recent_tail": tail}


def _documents_agent_audit_context(job_id: str | None) -> list[dict[str, Any]]:
    payload = []
    for item in read_events(job_id, "audit")[-8:]:
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        payload.append({
            "occurred_at": item.get("occurred_at"),
            "action": item.get("action"),
            "status": item.get("status"),
            "details": {str(key): _documents_agent_sanitize_text(value, limit=160) for key, value in list(details.items())[:6]},
        })
    return payload


def _documents_agent_build_readonly_context(message: str, job_id: str | None, documents_status: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_job_paths(job_id)
    state_dir = resolve_state_path("generator", job_id).parent
    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    generator_details = load_agent_state("generator", {}, job_id=job_id, include_details=True)
    philologist_details = load_agent_state("philologist", {}, job_id=job_id, include_details=True)
    service_docs = format_service_rag_context(find_relevant_service_docs(message, limit=3))
    context = {
        "screen": "documents",
        "job_id": job_id or "",
        "read_only": True,
        "user_question": _documents_agent_sanitize_text(message, limit=600),
        "service_knowledge": _documents_agent_sanitize_text(service_docs, limit=2500),
        "status": {
            "status": documents_status.get("status"),
            "stage": documents_status.get("stage"),
            "stage_text": documents_status.get("stage_text"),
            "progress_percent": documents_status.get("progress_percent"),
            "total_rows": documents_status.get("total_rows"),
            "processed_rows": documents_status.get("processed_rows"),
            "total_documents": documents_status.get("total_documents"),
            "reviewed_documents": documents_status.get("reviewed_documents"),
            "error_rows": documents_status.get("error_rows"),
            "fixed_documents": documents_status.get("fixed_documents"),
            "documents_with_issues": documents_status.get("documents_with_issues"),
            "output_file_count": documents_status.get("output_file_count"),
            "output_docx_count": documents_status.get("output_docx_count"),
            "output_pdf_count": documents_status.get("output_pdf_count"),
            "document_mode": documents_status.get("document_mode"),
            "work_type": documents_status.get("work_type"),
        },
        "generator": {
            "status": generator.get("status"),
            "stage": generator.get("stage"),
            "stage_text": generator.get("stage_text"),
            "summary_text": _documents_agent_sanitize_text(generator.get("summary_text"), limit=400),
            "total_rows": generator.get("total_rows"),
            "processed_rows": generator.get("processed_rows"),
            "error_rows": generator.get("error_rows"),
            "staged_docx_count": generator.get("staged_docx_count"),
            "staged_pdf_count": generator.get("staged_pdf_count"),
            "pdf_total": generator.get("pdf_total"),
            "pdf_processed": generator.get("pdf_processed"),
            "output_file_count": generator.get("output_file_count"),
            "renderer_version": generator.get("renderer_version"),
            "details": _documents_agent_generator_details(generator_details),
        },
        "philologist": {
            "status": philologist.get("status"),
            "summary_text": _documents_agent_sanitize_text(philologist.get("summary_text"), limit=400),
            "total_documents": philologist.get("total_documents"),
            "processed_documents": philologist.get("processed_documents"),
            "fixed_documents": philologist.get("fixed_documents"),
            "documents_with_issues": philologist.get("documents_with_issues"),
            "details_keys": sorted(str(key) for key in philologist_details.keys())[:12],
        },
        "files": {
            "input_data": _documents_agent_file_info(paths.data_xlsx),
            "templates": _documents_agent_list_files(paths.templates_dir, limit=8),
            "output": _documents_agent_list_files(paths.output_dir, limit=20),
            "batch_docx": _documents_agent_list_files(paths.batch_docx_dir, limit=10),
            "batch_pdf": _documents_agent_list_files(paths.batch_pdf_dir, limit=10),
            "output_zip": _documents_agent_file_info(state_dir / "output.zip"),
        },
        "workers": _documents_agent_worker_diagnostics(state_dir),
        "audit_tail": _documents_agent_audit_context(job_id),
        "sender": _documents_agent_sender_context(job_id),
        "rusender_events": _documents_agent_rusender_context(job_id),
    }
    return context


def _documents_agent_build_llm_client():
    if OpenAI is None:
        return None
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    http_client = _build_openai_http_client()
    if http_client is not None:
        kwargs["http_client"] = http_client
    try:
        return OpenAI(**kwargs)
    except Exception:
        return None


def _documents_agent_should_use_readonly_ai(message: str) -> bool:
    lowered = message.lower()
    if any(token in lowered for token in ("/test-scroll", "тест бегунка", "тест скролла")):
        return False
    if any(token in lowered for token in ("привет", "здравств", "спасибо", "поняла", "понял", "окей")) or _documents_agent_has_word(lowered, "ок"):
        return False
    diagnostic_tokens = (
        "почему", "разбер", "объясн", "не понимаю", "странно", "что случ", "что произошло",
        "пуст", "не собрал", "не создал", "не сформ", "упал", "сломал", "не работает",
        "все прошло", "всё прошло", "нормально", "успешно", "документы собран", "документы готовы",
        "все готово", "всё готово", "готово", "собран", "сформиров",
    )
    return any(token in lowered for token in diagnostic_tokens)


def _documents_agent_readonly_ai_reply(message: str, documents_status: dict, job_id: str | None, *, force: bool = False) -> dict[str, Any] | None:
    if not force and not _documents_agent_should_use_readonly_ai(message):
        return None
    client = _documents_agent_build_llm_client()
    if client is None:
        return None
    context = _documents_agent_build_readonly_context(message, job_id, documents_status)
    context_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    if len(context_text) > _MAX_DIAGNOSTIC_TEXT:
        context_text = context_text[: _MAX_DIAGNOSTIC_TEXT - 1].rstrip() + "…"
    system_prompt = (
        "Ты read-only агент-консультант внутри экрана «Документы» сервиса подготовки КП и рассылки. "
        "Ты НЕ запускаешь генерацию, НЕ отправляешь письма, НЕ меняешь файлы и НЕ просишь пользователя выполнить опасные команды. "
        "Твоя задача — объяснить простым русским языком, что сейчас происходит, почему могла быть ошибка и какой безопасный следующий шаг. "
        "Используй только факты из диагностического контекста. Если фактов недостаточно, прямо скажи, чего не хватает. "
        "Не показывай JSON, stack trace, пути файлов, токены, ключи, внутренние имена функций и сырые логи. "
        "Техническую причину переводи на человеческий язык. Ответ должен быть коротким: 1-4 предложения."
    )
    try:
        response = client.chat.completions.create(
            model=settings.case_agent_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Диагностический контекст:\n{context_text}\n\nВопрос пользователя: {message}"},
            ],
            temperature=0.2,
            max_tokens=420,
        )
    except Exception:
        return None
    reply = str(response.choices[0].message.content if response.choices else "").strip()
    if not reply:
        return None
    return _documents_agent_reply_payload(
        reply,
        source="documents_readonly_agent",
        tools_used=["get_documents_status", "inspect_job_state", "inspect_output_files", "inspect_worker_logs"],
    )

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
    rag_context = format_service_rag_context(find_relevant_service_docs(message, limit=3))
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
        f"Справка RAG по сервису:\n{rag_context}\n\n"
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

    if _documents_agent_is_capabilities_question(lowered):
        documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
        return _documents_agent_reply_payload(
            _documents_agent_capabilities_reply(documents_status),
            tools_used=["get_documents_status"],
        )

    if _documents_agent_is_ack_or_greeting(lowered):
        return _documents_agent_reply_payload(_documents_agent_general_reply(message), tools_used=[])
    if any(token in lowered for token in ("ты агент", "ты вообще агент", "ты завис", "завис", "отвечаешь")):
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

    documents_status = _documents_agent_tool_get_documents_status(job_id, status_loader)
    rag_reply = _documents_agent_rag_reply(message, documents_status)
    if rag_reply:
        return _documents_agent_reply_payload(rag_reply, source="service_rag", tools_used=["service_rag"])

    readonly_agent_reply = _documents_agent_readonly_ai_reply(message, documents_status, job_id)
    if readonly_agent_reply:
        return readonly_agent_reply

    if any(
        token in lowered
        for token in (
            "все прошло", "всё прошло", "нормально", "успешно", "документы собран", "документы готовы",
            "все готово", "всё готово", "готово", "собран", "сформиров",
        )
    ):
        return _documents_agent_reply_payload(
            _documents_agent_process_reply(documents_status),
            tools_used=["get_documents_status"],
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

    readonly_agent_reply = _documents_agent_readonly_ai_reply(message, documents_status, job_id, force=True)
    if readonly_agent_reply:
        return readonly_agent_reply

    return _documents_agent_reply_payload(
        _documents_agent_ai_unavailable_reply(),
        source="documents_ai_unavailable",
        tools_used=["get_documents_status"],
    )
