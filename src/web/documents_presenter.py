from __future__ import annotations

import re
from typing import Any

from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, document_mode_kinds, normalize_document_mode


def _safe_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _build_step_track(*, status: str, stage: str) -> list[dict[str, str]]:
    steps = [
        {"id": "generate", "state": "idle"},
        {"id": "review", "state": "idle"},
        {"id": "ready", "state": "idle"},
    ]
    active_index = -1
    if status == "completed":
        active_index = 2
    elif status in {"running", "waiting_review"}:
        if stage == "review" or status == "waiting_review":
            active_index = 1
        elif stage in {"ready", "convert_pdf", "finalize_output"}:
            active_index = 2
        else:
            active_index = 0
    elif status in {"error", "stopped"}:
        if stage == "review":
            active_index = 1
        elif stage in {"ready", "convert_pdf", "finalize_output"}:
            active_index = 2
        else:
            active_index = 0

    for index, step in enumerate(steps):
        if status == "completed":
            step["state"] = "done"
        elif index < active_index:
            step["state"] = "done"
        elif index == active_index:
            step["state"] = "error" if status == "error" else "active"
    return steps


def build_documents_ui_payload(documents_status: dict, *, readiness: dict) -> dict:
    generator_ready = bool(readiness.get("generator_ready"))
    generator_reason = str(readiness.get("generator_reason") or "").strip()

    status = str(documents_status.get("status") or "idle")
    stage = str(documents_status.get("stage") or "generate")
    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    total_rows = max(0, int(documents_status.get("total_rows") or 0))
    processed_rows = max(0, int(documents_status.get("processed_rows") or 0))
    total_documents = max(0, int(documents_status.get("total_documents") or 0))
    reviewed_documents = max(0, int(documents_status.get("reviewed_documents") or 0))
    fixed_documents = max(0, int(documents_status.get("fixed_documents") or 0))
    output_file_count = max(0, int(documents_status.get("output_file_count") or 0))
    output_pdf_count = max(0, int(documents_status.get("output_pdf_count") or 0))
    document_mode = normalize_document_mode(documents_status.get("document_mode") or generator.get("document_mode") or DOCUMENT_MODE_BOTH)
    documents_per_row = len(document_mode_kinds(document_mode))
    pdfs_per_row = 1 if "kp" in document_mode_kinds(document_mode) else 0
    staged_docx_count = max(0, int(generator.get("staged_docx_count") or 0))
    generated_docx_count = max(staged_docx_count, int(generator.get("generated_docx_count") or 0))
    pdf_total = max(0, int(generator.get("pdf_total") or 0))
    pdf_processed = max(0, int(generator.get("pdf_processed") or generator.get("staged_pdf_count") or 0))
    expected_documents = total_rows * documents_per_row if total_rows > 0 else max(total_documents, generated_docx_count, output_file_count)
    expected_pdf_documents = total_rows * pdfs_per_row if total_rows > 0 else pdf_total
    shown_documents = max(generated_docx_count, total_documents if stage in {"review", "completed"} else 0)
    shown_pdf_total = expected_pdf_documents or pdf_total
    if shown_pdf_total <= 0:
        shown_pdf_done = 0
    elif status == "completed" and output_pdf_count <= 0 and pdf_processed <= 0:
        shown_pdf_done = shown_pdf_total
    else:
        shown_pdf_done = max(pdf_processed, min(output_pdf_count, shown_pdf_total))
    clients_text = f"{processed_rows} из {total_rows} клиентов" if total_rows > 0 else "клиентов пока не найдено"
    documents_text = f"{shown_documents} из {expected_documents} документов"
    pdf_text = f"{shown_pdf_done} из {shown_pdf_total} файлов"

    process_title = "Готово к запуску" if generator_ready else "Нужно подготовить входные данные"
    process_main = (
        "Сервис подготовит документы по вашей таблице."
        if generator_ready
        else "Подготовку документов пока нельзя запустить."
    )
    process_detail = (
        "После запуска ничего дополнительно делать не нужно."
        if generator_ready
        else (generator_reason or "Сначала загрузите таблицу и шаблоны.")
    )
    process_next = (
        "Когда всё будет готово, можно будет скачать результат и перейти дальше."
        if generator_ready
        else "Сначала завершите подготовку таблицы и шаблонов."
    )
    badge_text = "Готов к запуску" if generator_ready else "Ожидание данных"
    badge_tone = "idle" if generator_ready else "wait"
    run_text = "Подготовить документы"
    label_text = str(documents_status.get("stage_text") or "Подготовка документов ещё не запускалась.")
    generator_hint = generator_reason or ("Можно запускать. Дальше сервис всё сделает сам." if generator_ready else "Сначала загрузите таблицу и шаблоны.")
    actions_hint = "Можно запускать. Дальше сервис всё сделает сам." if generator_ready else (generator_reason or "Сначала загрузите таблицу и шаблоны.")
    next_hint = "Кнопка перехода дальше включится автоматически после завершения подготовки."
    next_button_text = "Дальше: проверить отправку"
    next_button_title = "Сначала завершите подготовку документов."
    current_item_text = _safe_label(documents_status.get("current_item_text"))
    progress_percent = max(0, min(100, int(documents_status.get("progress_percent") or 0)))
    if status == "completed":
        progress_percent = 100
    elif status == "idle":
        progress_percent = 0
    progress_running = status == "running"
    restart_locked = bool(documents_status.get("restart_locked"))
    restart_disabled_reason = (
        "Документы уже успешно подготовлены без ошибок. Повторный запуск для этой сессии заблокирован."
        if restart_locked
        else ""
    )
    done_value = processed_rows
    done_label = "Клиентов"

    if status == "running":
        process_title = "Идёт подготовка"
        process_main = (
            "Проверяем текст."
            if stage == "review"
            else "Собираем результат."
            if stage == "ready"
            else "Готовим документы."
        )
        process_detail = ""
        process_next = "Скоро сервис перейдёт к следующему этапу."
        badge_text = "Проверяем текст" if stage == "review" else "Подготовка"
        badge_tone = "progress"
        run_text = "Документы готовятся"
        if stage == "review" and total_documents > 0:
            label_text = f"Проверено документов: {reviewed_documents} из {total_documents}."
        elif str(generator.get("stage") or "") == "convert_pdf":
            label_text = f"Собираем результат: {shown_pdf_done} из {shown_pdf_total} файлов."
        elif str(generator.get("stage") or "") == "finalize_output":
            label_text = "Собираем итоговый результат."
        elif expected_documents > 0 and shown_documents > 0:
            label_text = f"Создано документов: {shown_documents} из {expected_documents}."
        elif total_rows > 0:
            label_text = f"Готовим данные клиентов: {processed_rows} из {total_rows}. Документы появятся после обработки первых строк."
        generator_hint = (
            f"Проверяем текст: {reviewed_documents} из {total_documents} документов."
            if stage == "review"
            else f"Собираем результат: {shown_pdf_done} из {shown_pdf_total} файлов."
            if str(generator.get("stage") or "") == "convert_pdf"
            else "Собираем итоговый результат."
            if str(generator.get("stage") or "") == "finalize_output"
            else f"Готовим документы: {processed_rows} из {total_rows} клиентов."
        )
        actions_hint = "Идёт подготовка документов. Просто дождитесь завершения."
    elif status == "completed":
        process_title = "Готово"
        process_main = "Результат собран."
        process_detail = (
            f"Готовы комплекты для {total_rows} клиентов. Проверено {reviewed_documents} из {total_documents} документов."
            if total_rows > 0
            else "Подготовка завершена."
        )
        process_next = "Теперь можно скачать результат или перейти к проверке отправки."
        badge_text = "Готово"
        badge_tone = "done"
        run_text = "Подготовка завершена" if restart_locked else "Подготовить заново"
        label_text = "Документы подготовлены. Можно скачать результат и перейти к проверке отправки."
        generator_hint = "Документы готовы. Можно скачать результат."
        actions_hint = restart_disabled_reason if restart_locked else "Готово. Можно скачать документы."
        next_hint = "Теперь можно переходить к следующему шагу."
        next_button_title = "Перейти к проверке отправки."
        done_value = total_rows or total_documents // 2
    elif status == "stopped":
        process_title = "Остановлено"
        process_main = "Подготовка остановлена."
        process_detail = "Прогресс сохранён. Можно продолжить с этого места."
        process_next = "Когда будете готовы, нажмите кнопку продолжения."
        badge_text = "Остановлено"
        badge_tone = "wait"
        run_text = "Продолжить подготовку"
        generator_hint = "Подготовка остановлена. Можно продолжить с сохраненного места."
        actions_hint = "Следующий шаг недоступен, пока подготовка не будет завершена."
    elif status == "error":
        process_title = "Ошибка"
        process_main = "Подготовка документов не завершилась."
        process_detail = str(documents_status.get("summary_text") or "Не удалось завершить подготовку документов.")
        process_next = "Исправьте проблему и повторите запуск."
        badge_text = "Ошибка"
        badge_tone = "error"
        run_text = "Повторить подготовку"
        generator_hint = process_detail
        actions_hint = "Сначала повторите подготовку документов."
    elif status == "waiting_review":
        process_title = "Проверка ожидается"
        process_main = "Документы уже созданы."
        process_detail = ""
        process_next = "После проверки текста можно будет скачать результат и перейти дальше."
        badge_text = "Ожидает проверки"
        badge_tone = "wait"
        run_text = "Продолжить подготовку"
        generator_hint = "Документы созданы. Следующий этап запустится автоматически."
        actions_hint = "Дождитесь завершения проверки текста."

    return {
        "process": {
            "title": process_title,
            "main": process_main,
            "detail": process_detail,
            "next": process_next,
            "current_item_text": current_item_text,
            "clients_text": clients_text,
            "documents_text": documents_text,
            "pdf_text": pdf_text,
            "clients_total": total_rows,
            "clients_done": processed_rows,
            "documents_total": expected_documents,
            "documents_done": shown_documents,
            "review_total": total_documents,
            "review_done": reviewed_documents,
            "show_review": total_documents > 0 and (
                stage == "review"
                or status == "completed"
                or str(philologist.get("status") or "") in {"running", "finalizing", "completed", "stopped", "error"}
            ),
            "steps": _build_step_track(status=status, stage=stage),
        },
        "module": {
            "badge_text": badge_text,
            "badge_tone": badge_tone,
            "run_text": run_text,
            "label_text": label_text,
            "generator_hint": generator_hint,
            "philologist_hint": "",
            "actions_hint": actions_hint,
            "next_hint": next_hint,
            "done_value": done_value,
            "done_label": done_label,
            "error_value": int(documents_status.get("error_rows") or 0),
            "total_value": total_rows,
        },
        "progress": {
            "percent": progress_percent,
            "running": progress_running,
        },
        "actions": {
            # The start endpoint still validates files/templates. Keep the UI button clickable
            # so stale readiness polling cannot trap the user on a disabled action.
            "can_run": status in {"idle", "completed", "stopped", "error", "waiting_review"} and not restart_locked,
            "can_stop": status == "running",
            "can_download_output": status == "completed" and output_file_count > 0,
            "can_download_report": status == "completed" and (fixed_documents > 0 or total_documents > 0),
            "can_go_next": status == "completed",
            "next_button_text": next_button_text,
            "next_button_title": next_button_title,
            "run_disabled_reason": restart_disabled_reason,
        },
        "chat_events": build_documents_chat_events(documents_status),
    }


def _is_documents_failure_event_text(text: str) -> bool:
    lowered = text.casefold()
    return any(fragment in lowered for fragment in (
        "результат не собран",
        "не найдены ожидаемые документы",
        "документы не созданы",
        "завершились ошибкой",
        "пустой архив",
    ))


def _documents_recent_events_payload(
    events: list[dict] | None,
    *,
    prefix: str,
    title: str,
    limit: int = 3,
    suppress_failure_events: bool = False,
) -> list[dict]:
    payload: list[dict] = []
    for event in (events or [])[:limit]:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        text = str(event.get("message") or event.get("summary") or event.get("event") or "").strip()
        if not event_id or not text:
            continue
        lowered = text.lower()
        if suppress_failure_events and _is_documents_failure_event_text(text):
            continue
        if any(fragment in lowered for fragment in (
            "принято документов",
            "фильтр строк",
            "прочитан журнал склонений",
            "план агента",
            "цикл исполнения",
            "уже готово",
            "из 100 клиентов",
            "из 200",
            "проверяю текст в документах:",
            "конвертирую docx",
            "pdf",
        )):
            continue
        payload.append({
            "id": f"{prefix}:{event_id}",
            "title": title,
            "text": text.rstrip(".") + ".",
        })
    return payload


def _dedupe_documents_chat_events(events: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen_keys: set[str] = set()
    for item in events:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        if not event_id or not text:
            continue
        key = re.sub(r"\s+", " ", f"{title}\n{text}".strip().lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append({
            "id": event_id,
            "title": title,
            "text": text,
        })
    return deduped


def build_documents_chat_events(documents_status: dict) -> list[dict]:
    status = str(documents_status.get("status") or "idle")
    stage = str(documents_status.get("stage") or "generate")
    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    generator_stage = str(generator.get("stage") or "")
    total_rows = max(0, int(documents_status.get("total_rows") or 0))

    events: list[dict] = []
    if status == "idle":
        return events

    if status in {"running", "waiting_review", "completed", "stopped", "error"} and total_rows > 0:
        events.append({
            "id": f"documents:start:{total_rows}",
            "title": "Подготовка началась",
            "text": f"Запускаю подготовку документов. Всего клиентов: {total_rows}.",
        })

    if stage == "generate":
        if generator_stage == "review_templates":
            events.append({
                "id": "documents:stage:review_templates",
                "title": "Проверяю шаблоны",
                "text": "Проверяю шаблоны перед созданием документов.",
            })
        elif generator_stage == "render_docx":
            events.append({
                "id": "documents:stage:render_docx",
                "title": "Создаю документы",
                "text": "Создаю документы по шаблонам.",
            })
    elif stage == "ready":
        events.append({
            "id": "documents:stage:finalize_output",
            "title": "Собираю результат",
            "text": "Проверка текста завершена. Собираю итоговый результат.",
        })
    elif stage == "review":
        events.append({
            "id": "documents:stage:review",
            "title": "Проверяю текст",
            "text": "Проверяю текст в готовых документах.",
        })

    if status == "waiting_review":
        events.append({
            "id": "documents:waiting_review",
            "title": "Документы созданы",
            "text": "Документы уже созданы. Следующий этап: проверка текста.",
        })
    elif status == "completed":
        events.append({
            "id": "documents:completed",
            "title": "Подготовка завершена",
            "text": "Результат собран. Можно скачать архив и перейти к проверке отправки.",
        })
    elif status == "stopped":
        events.append({
            "id": "documents:stopped",
            "title": "Подготовка остановлена",
            "text": "Подготовка остановлена. Можно продолжить с сохраненного места.",
        })
    elif status == "error":
        events.append({
            "id": "documents:error",
            "title": "Ошибка подготовки",
            "text": str(documents_status.get("summary_text") or "Не удалось завершить подготовку документов.").strip(),
        })

    recent_generator_events = _documents_recent_events_payload(
        generator.get("recent_events"),
        prefix="generator",
        title="Дополнительно",
        suppress_failure_events=status == "completed",
    )
    recent_philologist_events = _documents_recent_events_payload(philologist.get("recent_events"), prefix="philologist", title="Проверка текста")

    if status == "running" and generator_stage in {"review_templates", "render_docx", "convert_pdf", "finalize_output"}:
        recent_generator_events = recent_generator_events[:1]
    if status == "running" and stage == "review":
        recent_philologist_events = recent_philologist_events[:1]

    events.extend(recent_generator_events)
    events.extend(recent_philologist_events)
    return _dedupe_documents_chat_events(events)
