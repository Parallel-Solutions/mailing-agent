from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_deps: dict[str, Any] = {}


def configure_documents_service(**deps: Any) -> None:
    _deps.update(deps)


def _require(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"documents_service dependency is not configured: {name}")
    return value


def _documents_generator_percent(generator_state: dict) -> int:
    status = str(generator_state.get("status") or "idle")
    stage = str(generator_state.get("stage") or "")
    total = max(0, int(generator_state.get("total_rows") or 0))
    processed = max(0, int(generator_state.get("processed_rows") or 0))
    pdf_total = max(0, int(generator_state.get("pdf_total") or 0))
    pdf_processed = max(
        0,
        int(generator_state.get("pdf_processed") or generator_state.get("staged_pdf_count") or 0),
    )
    if status == "completed":
        return 100
    if stage == "review_templates":
        return 5
    if stage == "render_docx":
        return min(60, 5 + round((processed / total) * 55)) if total else 5
    if stage == "convert_pdf":
        safe_pdf_total = pdf_total or max(int(generator_state.get("staged_docx_count") or 0), total, 1)
        return min(95, 60 + round((pdf_processed / safe_pdf_total) * 35))
    if stage == "finalize_output":
        return 97
    return min(95, round((processed / total) * 60)) if total else 0


def _documents_philologist_percent(philologist_state: dict) -> int:
    status = str(philologist_state.get("status") or "idle")
    total = max(0, int(philologist_state.get("total_documents") or 0))
    processed = max(0, int(philologist_state.get("processed_documents") or 0))
    if status == "completed":
        return 100
    if status == "finalizing":
        return 95
    return min(95, round((processed / total) * 100)) if total else 0


def _stop_orphaned_documents_worker_state(
    *,
    job_id: str | None,
    agent_name: str,
    state: dict,
    worker_thread: Any,
    pipeline_thread: Any,
) -> dict:
    status = str(state.get("status") or "idle")
    if status not in {"running", "finalizing"}:
        return state
    if worker_thread is not None or pipeline_thread is not None:
        return state

    recovered_state = dict(state)
    recovered_state["status"] = "stopped"
    recovered_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    recovered_state["stop_requested"] = False
    recovered_state["stop_requested_at"] = None
    recovered_state["summary_text"] = (
        "Работа была остановлена после перезапуска сервиса. "
        "Можно продолжить с сохраненного места."
    )
    if agent_name == "generator":
        _require("save_generator_state")(recovered_state, job_id)
    elif agent_name == "philologist":
        _require("save_philologist_state")(recovered_state, job_id)
    return recovered_state


def compact_documents_status(job_id: str | None) -> dict:
    compact_generator_status = _require("compact_generator_status")
    get_generator_status = _require("get_generator_status")
    compact_philologist_status = _require("compact_philologist_status")
    get_philologist_status = _require("get_philologist_status")
    get_documents_thread = _require("get_documents_thread")
    get_generator_thread = _require("get_generator_thread")
    get_philologist_thread = _require("get_philologist_thread")

    generator_state = compact_generator_status(get_generator_status(job_id))
    philologist_state = compact_philologist_status(get_philologist_status(job_id, include_details=False))
    pipeline_thread = get_documents_thread(job_id)
    generator_state = _stop_orphaned_documents_worker_state(
        job_id=job_id,
        agent_name="generator",
        state=generator_state,
        worker_thread=get_generator_thread(job_id),
        pipeline_thread=pipeline_thread,
    )
    philologist_state = _stop_orphaned_documents_worker_state(
        job_id=job_id,
        agent_name="philologist",
        state=philologist_state,
        worker_thread=get_philologist_thread(job_id),
        pipeline_thread=pipeline_thread,
    )
    generator_status = str(generator_state.get("status") or "idle")
    philologist_status = str(philologist_state.get("status") or "idle")
    generator_done = generator_status == "completed"
    reviewed_documents = int(philologist_state.get("processed_documents") or 0)
    total_documents = int(philologist_state.get("total_documents") or 0)
    philologist_done = philologist_status == "completed" or (
        total_documents > 0
        and reviewed_documents >= total_documents
        and philologist_status in {"running", "finalizing"}
    )

    if generator_status == "error" or philologist_status == "error":
        status = "error"
    elif generator_status == "stopped" or philologist_status == "stopped":
        status = "stopped"
    elif pipeline_thread is not None or generator_status == "running" or philologist_status in {"running", "finalizing"}:
        status = "running"
    elif generator_done and philologist_done:
        status = "completed"
    elif generator_done:
        status = "waiting_review"
    else:
        status = generator_status if generator_status in {"completed", "error", "stopped"} else "idle"

    if not generator_done:
        stage = "generate"
        stage_text = "Создаю документы."
        progress_percent = round(_documents_generator_percent(generator_state) * 0.7)
    elif not philologist_done:
        stage = "review"
        stage_text = "Проверяю готовые документы."
        progress_percent = 70 + round(_documents_philologist_percent(philologist_state) * 0.28)
    else:
        stage = "completed"
        stage_text = "Документы созданы и проверены."
        progress_percent = 100

    if status == "idle":
        stage_text = "Подготовка документов ещё не запускалась."
        progress_percent = 0
    elif status == "waiting_review":
        stage_text = "Документы созданы. Можно запустить проверку."
        progress_percent = max(progress_percent, 70)
    elif status == "stopped":
        stage_text = "Работа остановлена. Можно продолжить с сохраненного места."
    elif status == "error":
        stage_text = (
            generator_state.get("summary_text")
            or philologist_state.get("summary_text")
            or "Не удалось завершить подготовку документов."
        )

    total_rows = max(int(generator_state.get("total_rows") or 0), int(philologist_state.get("total_documents") or 0) // 2)
    processed_rows = int(generator_state.get("processed_rows") or 0)
    if generator_done and total_rows:
        processed_rows = total_rows

    result = {
        "status": status,
        "stage": stage,
        "stage_text": stage_text,
        "progress_percent": max(0, min(100, progress_percent)),
        "generator": generator_state,
        "philologist": philologist_state,
        "total_rows": total_rows,
        "processed_rows": processed_rows,
        "total_documents": total_documents,
        "reviewed_documents": reviewed_documents,
        "error_rows": int(generator_state.get("error_rows") or 0),
        "fixed_documents": int(philologist_state.get("fixed_documents") or 0),
        "documents_with_issues": int(philologist_state.get("documents_with_issues") or 0),
        "output_file_count": int(generator_state.get("output_file_count") or 0),
        "summary_text": stage_text,
    }
    result["ui"] = build_documents_ui_payload(result)
    return result


def build_documents_ui_payload(documents_status: dict) -> dict:
    status = str(documents_status.get("status") or "idle")
    stage = str(documents_status.get("stage") or "generate")
    generator = documents_status.get("generator") or {}
    philologist = documents_status.get("philologist") or {}
    total_rows = max(0, int(documents_status.get("total_rows") or 0))
    processed_rows = max(0, int(documents_status.get("processed_rows") or 0))
    total_documents = max(0, int(documents_status.get("total_documents") or 0))
    reviewed_documents = max(0, int(documents_status.get("reviewed_documents") or 0))
    error_rows = max(0, int(documents_status.get("error_rows") or 0))
    fixed_documents = max(0, int(documents_status.get("fixed_documents") or 0))
    output_file_count = max(0, int(documents_status.get("output_file_count") or 0))
    staged_docx_count = max(0, int(generator.get("staged_docx_count") or 0))
    pdf_total = max(0, int(generator.get("pdf_total") or 0))
    pdf_processed = max(0, int(generator.get("pdf_processed") or generator.get("staged_pdf_count") or 0))
    expected_documents = total_rows * 2 if total_rows > 0 else max(total_documents, pdf_total, staged_docx_count, output_file_count)
    shown_documents = max(staged_docx_count, pdf_total, total_documents, expected_documents if status == "completed" else 0)
    shown_pdf_total = expected_documents or pdf_total
    shown_pdf_done = shown_pdf_total if status == "completed" and output_file_count <= 0 and pdf_processed <= 0 else max(pdf_processed, min(output_file_count, shown_pdf_total or output_file_count))
    clients_text = f"{processed_rows} из {total_rows} клиентов" if total_rows > 0 else "клиентов пока не найдено"
    documents_text = f"{shown_documents} из {expected_documents} документов"
    pdf_text = f"{shown_pdf_done} из {shown_pdf_total} PDF"

    process_title = "Готово к запуску"
    process_main = "Сервис подготовит документы по вашей таблице."
    process_detail = "После запуска ничего дополнительно делать не нужно."
    process_next = "Когда всё будет готово, можно будет скачать результат и перейти дальше."
    badge_text = "Готов к запуску"
    badge_tone = "idle"
    run_text = "Подготовить документы"
    label_text = str(documents_status.get("stage_text") or "Подготовка документов ещё не запускалась.")
    generator_hint = "Сначала загрузите таблицу и шаблоны."
    actions_hint = "Можно запускать. Дальше сервис всё сделает сам."
    next_hint = "Кнопка перехода дальше включится автоматически после завершения подготовки."
    next_button_text = "Дальше: проверить отправку"
    next_button_title = "Сначала завершите подготовку документов."

    if status == "running":
        process_title = "Идёт подготовка"
        process_main = (
            "Сейчас сервис проверяет текст в документах."
            if stage == "review"
            else "Сейчас сервис сохраняет готовые файлы."
            if str(generator.get("stage") or "") in {"convert_pdf", "finalize_output"}
            else "Сейчас сервис создаёт документы."
        )
        process_detail = (
            f"Проверено {reviewed_documents} из {total_documents}. Ничего нажимать не нужно."
            if stage == "review" and total_documents > 0
            else f"Подготовлено {processed_rows} из {total_rows}. Ничего нажимать не нужно."
            if total_rows > 0
            else "Ничего нажимать не нужно."
        )
        process_next = "Скоро подготовка завершится автоматически."
        badge_text = "Проверка текстов" if stage == "review" else "Подготовка"
        badge_tone = "progress"
        run_text = "Проверяю документы" if stage == "review" else "Документы готовятся"
        generator_hint = (
            f"Документы созданы. Проверяю тексты: {reviewed_documents} из {total_documents}."
            if stage == "review"
            else f"Сохраняю документы в PDF: {pdf_processed} из {shown_pdf_total}."
            if str(generator.get("stage") or "") in {"convert_pdf", "finalize_output"}
            else f"Создаю документы по шаблонам: {processed_rows} из {total_rows}."
        )
        actions_hint = "Идёт подготовка документов. Просто дождитесь завершения."
    elif status == "completed":
        process_title = "Готово"
        process_main = "Документы подготовлены."
        process_detail = f"Подготовка завершена для {total_rows} клиентов." if total_rows > 0 else "Подготовка завершена."
        process_next = "Теперь можно скачать архив или перейти к проверке отправки."
        badge_text = "Готово"
        badge_tone = "done"
        run_text = "Подготовить заново"
        generator_hint = "Документы готовы. Безопасные правки внесены."
        actions_hint = "Готово. Можно скачать архив документов."
        next_hint = "Теперь можно переходить к следующему шагу."
        next_button_title = "Перейти к проверке отправки."
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
        process_detail = "Осталось завершить проверку текста."
        process_next = "После проверки текста можно будет скачать результат и перейти дальше."
        badge_text = "Ожидает проверки"
        badge_tone = "wait"
        run_text = "Продолжить подготовку"
        generator_hint = "Документы созданы. Следующий этап: проверка текста."
        actions_hint = "Дождитесь завершения проверки текста."

    return {
        "process": {
            "title": process_title,
            "main": process_main,
            "detail": process_detail,
            "next": process_next,
            "clients_text": clients_text,
            "documents_text": documents_text,
            "pdf_text": pdf_text,
        },
        "module": {
            "badge_text": badge_text,
            "badge_tone": badge_tone,
            "run_text": run_text,
            "label_text": label_text,
            "generator_hint": generator_hint,
            "actions_hint": actions_hint,
            "next_hint": next_hint,
        },
        "actions": {
            "can_run": status in {"idle", "completed", "stopped", "error", "waiting_review"},
            "can_stop": status == "running",
            "can_download_output": status == "completed" and output_file_count > 0,
            "can_download_report": status == "completed" and (fixed_documents > 0 or total_documents > 0),
            "can_go_next": status == "completed",
            "next_button_text": next_button_text,
            "next_button_title": next_button_title,
        },
        "chat_events": build_documents_chat_events(documents_status),
    }


def _documents_recent_events_payload(events: list[dict] | None, *, prefix: str, title: str, limit: int = 3) -> list[dict]:
    payload: list[dict] = []
    for event in (events or [])[:limit]:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        text = str(event.get("message") or event.get("summary") or event.get("event") or "").strip()
        if not event_id or not text:
            continue
        lowered = text.lower()
        if any(fragment in lowered for fragment in (
            "принято документов",
            "фильтр строк",
            "прочитан журнал склонений",
            "план агента",
            "цикл исполнения",
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
    processed_rows = max(0, int(documents_status.get("processed_rows") or 0))
    total_documents = max(0, int(documents_status.get("total_documents") or 0))
    reviewed_documents = max(0, int(documents_status.get("reviewed_documents") or 0))

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
            progress_percent = round((processed_rows / total_rows) * 100) if total_rows > 0 else 0
            progress_bucket = 50 if progress_percent >= 50 and progress_percent < 100 else 0
            events.append({
                "id": f"documents:stage:render_docx:{progress_bucket or 'start'}",
                "title": "Создаю документы",
                "text": (
                    f"Создаю документы по шаблонам. Уже готово {processed_rows} из {total_rows} клиентов."
                    if total_rows > 0 and progress_bucket >= 50
                    else "Создаю документы по шаблонам."
                ),
            })
        elif generator_stage == "convert_pdf":
            events.append({
                "id": "documents:stage:convert_pdf",
                "title": "Сохраняю PDF",
                "text": "Документы созданы. Сейчас сохраняю их в PDF.",
            })
        elif generator_stage == "finalize_output":
            events.append({
                "id": "documents:stage:finalize_output",
                "title": "Готовлю результат",
                "text": "PDF уже готовы. Собираю итоговые файлы и архив к скачиванию.",
            })
    elif stage == "review":
        review_percent = round((reviewed_documents / total_documents) * 100) if total_documents > 0 else 0
        review_bucket = 50 if review_percent >= 50 and review_percent < 100 else 0
        events.append({
            "id": f"documents:stage:review:{review_bucket or 'start'}",
            "title": "Проверяю текст",
            "text": (
                f"Проверяю текст в документах: {reviewed_documents} из {total_documents}."
                if total_documents > 0 and review_bucket >= 50
                else "Проверяю текст в готовых документах."
            ),
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
            "text": "Документы подготовлены. Можно скачать архив и перейти к проверке отправки.",
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

    recent_generator_events = _documents_recent_events_payload(generator.get("recent_events"), prefix="generator", title="Дополнительно")
    recent_philologist_events = _documents_recent_events_payload(philologist.get("recent_events"), prefix="philologist", title="Проверка текста")

    if status == "running" and generator_stage in {"review_templates", "render_docx", "convert_pdf", "finalize_output"}:
        recent_generator_events = recent_generator_events[:1]
    if status == "running" and stage == "review":
        recent_philologist_events = recent_philologist_events[:1]

    events.extend(recent_generator_events)
    events.extend(recent_philologist_events)
    return _dedupe_documents_chat_events(events)


def _documents_agent_recent_event_lines(state: dict, *, limit: int = 3) -> list[str]:
    lines: list[str] = []
    for event in (state.get("recent_events") or [])[:limit]:
        if not isinstance(event, dict):
            continue
        text = str(event.get("message") or event.get("summary") or event.get("event") or "").strip()
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
        return "создание документов"
    if generator_stage == "convert_pdf":
        return "сохранение PDF"
    if generator_stage == "finalize_output":
        return "подготовка файлов к скачиванию"
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
    stage_label = _documents_agent_stage_label(documents_status)

    if status == "running":
        parts = [f"Сейчас идёт {stage_label}."]
        if documents_status.get("stage") == "review" and total_documents > 0:
            parts.append(f"Проверено {reviewed_documents} из {total_documents} документов.")
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
        parts.append("Можно скачать архив документов и перейти к проверке отправки.")
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


def _documents_agent_should_delegate_to_philologist(message: str, documents_status: dict) -> bool:
    lowered = message.lower()
    philologist_keywords = (
        "филолог", "ошиб", "исправ", "правк", "грамот", "граммат", "орфограф",
        "пунктуа", "текст", "формулиров", "замечан", "правило", "документе"
    )
    if any(token in lowered for token in philologist_keywords):
        return True
    return int(documents_status.get("reviewed_documents") or 0) > 0 and any(
        token in lowered for token in ("что не так", "что исправ", "какие проблемы", "какие замечания")
    )


def _documents_pipeline_stop_requested(job_id: str | None) -> bool:
    generator_state = _require("load_generator_state")(job_id)
    philologist_state = _require("load_philologist_state")(job_id)
    return bool(generator_state.get("stop_requested")) or bool(philologist_state.get("stop_requested"))


def _mark_documents_waiting_review_stopped(job_id: str | None) -> None:
    philologist_state = _require("load_philologist_state")(job_id)
    if str(philologist_state.get("status") or "") == "completed":
        return
    philologist_state["status"] = "stopped"
    philologist_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    philologist_state["summary_text"] = (
        "Подготовка остановлена после создания документов. "
        "Проверку текста можно запустить позже."
    )
    _require("save_philologist_state")(philologist_state, job_id)


def documents_agent_choose_reply(message: str, job_id: str | None = None) -> dict[str, str]:
    documents_status = compact_documents_status(job_id)
    lowered = message.lower()

    if _documents_agent_should_delegate_to_philologist(message, documents_status):
        delegated = _require("chat_with_philologist")(message, job_id=job_id)
        reply = str(delegated.get("reply") or "").strip()
        if reply:
            return {"reply": reply}

    if any(token in lowered for token in ("что происходит", "на каком этапе", "статус", "идет ли", "что сейчас", "процесс")):
        return {"reply": _documents_agent_process_reply(documents_status)}
    if any(token in lowered for token in ("что дальше", "следующ", "можно ли дальше", "переход")):
        return {"reply": _documents_agent_next_step_reply(documents_status)}
    if any(token in lowered for token in ("скачать", "архив", "отч", "файл", "pdf", "docx")):
        return {"reply": _documents_agent_download_reply(documents_status)}
    if any(token in lowered for token in ("итог", "результат", "сколько готово", "сколько документов", "сводк")):
        return {"reply": _documents_agent_result_reply(documents_status)}

    process_reply = _documents_agent_process_reply(documents_status)
    next_reply = _documents_agent_next_step_reply(documents_status)
    return {"reply": f"{process_reply} {next_reply}"}


def run_documents_pipeline_background(*, xlsx_path: Path, job_id: str | None, mode: str | None) -> None:
    try:
        get_generator_status = _require("get_generator_status")
        clear_generator_stop_request = _require("clear_generator_stop_request")
        run_generator_agent = _require("run_generator_agent")
        get_philologist_status = _require("get_philologist_status")
        clear_philologist_stop_request = _require("clear_philologist_stop_request")
        run_philologist = _require("run_philologist")
        schedule_output_archive_build = _require("schedule_output_archive_build")

        generator_state = get_generator_status(job_id)
        if str(generator_state.get("status") or "") != "completed":
            clear_generator_stop_request(job_id)
            generator_state = run_generator_agent(xlsx_path=xlsx_path, job_id=job_id)

        if str(generator_state.get("status") or "") != "completed":
            return

        if _documents_pipeline_stop_requested(job_id):
            _mark_documents_waiting_review_stopped(job_id)
            return

        philologist_state = get_philologist_status(job_id, include_details=False)
        if str(philologist_state.get("status") or "") != "completed":
            if _documents_pipeline_stop_requested(job_id):
                _mark_documents_waiting_review_stopped(job_id)
                return
            clear_philologist_stop_request(job_id)
            philologist_state = run_philologist(ai_enabled=True, job_id=job_id, mode=mode or "fast")

        if isinstance(philologist_state, dict) and philologist_state.get("status") == "completed":
            schedule_output_archive_build(job_id)
    except Exception as exc:
        _require("logger").exception("documents_pipeline_failed", job_id=job_id)
        generator_state = _require("load_generator_state")(job_id)
        philologist_state = _require("load_philologist_state")(job_id)
        if str(generator_state.get("status") or "") == "running":
            generator_state["status"] = "error"
            generator_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
            generator_state["summary_text"] = f"Подготовка документов остановилась с ошибкой: {type(exc).__name__}: {exc}"
            _require("save_generator_state")(generator_state, job_id)
        elif str(philologist_state.get("status") or "") in {"running", "finalizing"}:
            philologist_state["status"] = "error"
            philologist_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
            philologist_state["summary_text"] = f"Проверка документов остановилась с ошибкой: {type(exc).__name__}: {exc}"
            _require("save_philologist_state")(philologist_state, job_id)
    finally:
        _require("unregister_documents_thread")(job_id)
