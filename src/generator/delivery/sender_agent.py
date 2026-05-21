from __future__ import annotations

import imaplib
import smtplib
import json
import re
import base64
import mimetypes
import secrets
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from docx import Document

from src.generator.inflection.ai_case_agent import (
    OpenAI,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.generator.orchestration.agent_handoff import (
    count_tasks_for_agent,
    create_task,
    get_recent_events,
    get_tasks_for_agent,
)
from src.generator.generation.config_generator import DATA_DIR, DATA_XLSX_PATH, OUTPUT_DIR, START_OUTGOING_NUMBER, TEMPLATES_DIR
from src.generator.generation.excel_io import load_rows, save_workbook, update_status
from src.generator.generation.generator_agent import run_generator_agent
from src.generator.case_engine import build_inflected_fields_with_trace
from src.generator.philologist.philologist_agent import run_philologist
from src.generator.orchestration.responsibility_matrix import diagnose_responsibility
from src.jobs import load_agent_state, resolve_job_paths, save_agent_state
from src.utils.config import settings


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAIL_TEMPLATE_PATH = TEMPLATES_DIR / "mail_template.txt"
MAIL_TEMPLATE_DOCX_PATH = TEMPLATES_DIR / "mail_template.docx"
SENT_MAIL_LOG_PATH = DATA_DIR / "sent_mail_log.jsonl"
DEFAULT_MAIL_SUBJECT = "Коммерческое предложение на разработку МНГП."
DEFAULT_MAIL_BODY = (
    "Добрый день!\n"
    "Направляем для рассмотрения коммерческое предложение на выполнение работ по разработке проекта "
    "местных нормативов градостроительного проектирования.\n"
    "Во вложении:\n"
    "— коммерческое предложение;\n"
    "— проект договора;\n"
    "— проект технического задания;\n"
    "— календарный план выполнения работ.\n"
    "\n"
    "ООО «Параллельные Решения» специализируется на разработке документов территориального планирования "
    "и градостроительного зонирования. В состав работ входят сбор и анализ исходных данных, подготовка "
    "проектных материалов и сопровождение согласования проекта до его утверждения.\n"
    "Просим передать материалы должностному лицу, курирующему вопросы архитектуры и градостроительства. "
    "Готовы провести рабочую консультацию по составу работ, срокам, порядку взаимодействия и ответить "
    "на вопросы в формате ВКС."
)
DEFAULT_MAIL_FOOTER_TEXT = (
    "С уважением,\n"
    "Черкашина Наталья Александровна\n"
    "+7 (812) 242-93-12\n"
    "ООО «Параллельные Решения»\n"
    "https://www.parresh.ru/"
)
MAIL_FOOTER_LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "parresh-signature-logo.png"
MAIL_FOOTER_LOGO_CID = "parresh-signature-logo"
MAIL_FOOTER_HTML_TEMPLATE = """
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:16px;border-collapse:collapse">
  <tr><td style="padding:0"><img src="{image_src}" alt="Параллельные Решения" width="340" style="display:block;width:340px;max-width:340px;height:auto;border:0;outline:none;text-decoration:none"></td></tr>
  <tr><td style="padding:6px 0 0 0;font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;font-size:13px;line-height:1.3"><a href="https://www.parresh.ru/" style="color:#1f5da8;text-decoration:underline">https://www.parresh.ru/</a></td></tr>
</table>
""".strip()
STATUS_SENT_VALUE = "Отправлено"
STATUS_ERROR_VALUE = "Ошибка"
STATUS_OK_VALUES = {"ОК", "OK", "SENT", "ОТПРАВЛЕНО", "ОТПРАВЛЕНО (ОК)"}
MAX_STATUS_ERROR_LENGTH = 240
UNISENDER_GO_SEND_PATH = "email/send.json"
UNISENDER_CLASSIC_SEND_PATH = "sendEmail"
UNISENDER_CLASSIC_CHECK_PATH = "checkEmail"


SENDER_STATE: dict[str, Any] = {
    "status": "idle",
    "mode": "dry_run",
    "started_at": None,
    "completed_at": None,
    "processed_rows": 0,
    "ready_rows": 0,
    "sent_rows": 0,
    "error_rows": 0,
    "skipped_rows": 0,
    "handoff_rows": 0,
    "total_rows": 0,
    "summary_text": "Агент-отправщик ещё не запускался.",
    "rows": [],
    "stats": {},
    "warning_rows": 0,
    "task_stats": {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0},
    "tasks": [],
    "recent_events": [],
    "generator_handoff_rows": 0,
    "philology_blocked_rows": 0,
    "autonomous_recovery_rows": 0,
    "effective_limit": None,
    "remaining_rows": 0,
    "stop_requested": False,
    "stop_requested_at": None,
    "transport": "smtp",
}

SENDER_STATE_ROWS_LIMIT = 200
SENDER_WORKBOOK_SAVE_EVERY = 25
UNISENDER_RETRY_ATTEMPTS = 3
UNISENDER_RETRY_BASE_SECONDS = 2.0
UNISENDER_RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
UNISENDER_REQUESTS_PER_MINUTE = 60
UNISENDER_MIN_REQUEST_INTERVAL_SECONDS = 60.0 / UNISENDER_REQUESTS_PER_MINUTE
_UNISENDER_RATE_LIMIT_LOCK = threading.Lock()
_last_unisender_request_at = 0.0


def _load_sender_state(job_id: str | None = None) -> dict[str, Any]:
    return load_agent_state("sender", SENDER_STATE, job_id)


def _save_sender_state(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    return save_agent_state("sender", state, job_id)


def _state_rows_snapshot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) <= SENDER_STATE_ROWS_LIMIT:
        return list(rows)
    return list(rows[-SENDER_STATE_ROWS_LIMIT:])


def _flush_sender_workbook(workbook: Any, data_xlsx_path: Path) -> str:
    try:
        save_workbook(workbook, data_xlsx_path)
        return ""
    except Exception as exc:
        return f"Не удалось сохранить изменения в data.xlsx: {_safe_text(exc) or exc}"


def _should_flush_sender_workbook(*, dirty: bool, processed_rows: int, total_rows: int) -> bool:
    if not dirty:
        return False
    if processed_rows <= 0:
        return False
    if processed_rows >= total_rows:
        return True
    return processed_rows % SENDER_WORKBOOK_SAVE_EVERY == 0


def _sleep_sender_retry(delay_seconds: float) -> None:
    if delay_seconds <= 0:
        return
    sleep(delay_seconds)


def _is_retryable_unisender_exception(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return int(exc.code) in UNISENDER_RETRYABLE_HTTP_CODES
    return isinstance(exc, (TimeoutError, URLError, OSError))


def _wait_unisender_api_slot() -> None:
    global _last_unisender_request_at
    with _UNISENDER_RATE_LIMIT_LOCK:
        now = perf_counter()
        wait_seconds = UNISENDER_MIN_REQUEST_INTERVAL_SECONDS - (now - _last_unisender_request_at)
        if wait_seconds > 0:
            sleep(wait_seconds)
        _last_unisender_request_at = perf_counter()


def _run_unisender_request(request: Request, *, timeout: float, request_label: str) -> str:
    last_error: Exception | None = None
    for attempt in range(1, UNISENDER_RETRY_ATTEMPTS + 1):
        try:
            _wait_unisender_api_slot()
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if attempt < UNISENDER_RETRY_ATTEMPTS and _is_retryable_unisender_exception(exc):
                _sleep_sender_retry(UNISENDER_RETRY_BASE_SECONDS * attempt)
                last_error = RuntimeError(
                    f"{request_label} временно недоступен (HTTP {exc.code}), повтор {attempt}."
                )
                continue
            exc.raw_body = raw  # type: ignore[attr-defined]
            raise
        except Exception as exc:
            if attempt < UNISENDER_RETRY_ATTEMPTS and _is_retryable_unisender_exception(exc):
                _sleep_sender_retry(UNISENDER_RETRY_BASE_SECONDS * attempt)
                last_error = exc
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{request_label} не ответил после повторных попыток.")


def _resolve_sender_data_xlsx_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_id:
        return job_paths.data_xlsx
    return job_paths.data_xlsx if job_paths.data_xlsx.exists() else DATA_XLSX_PATH


def _refresh_sender_stop_flag(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    persisted = _load_sender_state(job_id)
    state["stop_requested"] = bool(persisted.get("stop_requested", False))
    state["stop_requested_at"] = persisted.get("stop_requested_at")
    return state


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class _SafeMailTemplateValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _read_docx_mail_template(template_path: Path) -> str:
    document = Document(template_path)
    blocks: list[str] = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            blocks.append(text)
        elif blocks and blocks[-1] != "":
            blocks.append("")

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append("\t".join(cells))

    return "\n".join(blocks).strip()


def _read_mail_template(mail_template_path: Path | None = None) -> str:
    if mail_template_path is None:
        return DEFAULT_MAIL_BODY
    template_paths = [mail_template_path]
    for template_path in [path for path in template_paths if path is not None]:
        if not template_path.exists():
            continue
        try:
            if template_path.suffix.lower() == ".docx":
                text = _read_docx_mail_template(template_path)
            else:
                text = template_path.read_text(encoding="utf-8-sig").strip()
            if text:
                return text
        except OSError:
            continue
    return DEFAULT_MAIL_BODY


def _mail_outgoing_number(row: dict[str, Any]) -> str:
    raw_id = _safe_text(row.get("ID"))
    try:
        row_number = int(float(raw_id))
    except (TypeError, ValueError):
        return raw_id
    return str(START_OUTGOING_NUMBER + row_number - 1)


def _mail_mun_name_genitive(mun_name: str, fallback: str = "") -> str:
    normalized = re.sub(r"\s+", " ", _safe_text(mun_name)).strip()
    fallback = _safe_text(fallback)
    if fallback:
        return fallback
    if normalized.startswith("Городское поселение "):
        return f"Городского поселения {normalized[len('Городское поселение ') :].strip()}".strip()
    if normalized.startswith("Сельское поселение "):
        return f"Сельского поселения {normalized[len('Сельское поселение ') :].strip()}".strip()
    return normalized


def _mail_template_values(row: dict[str, Any]) -> dict[str, str]:
    outgoing_number = _mail_outgoing_number(row)
    try:
        inflected, _ = build_inflected_fields_with_trace(row)
    except Exception:
        inflected = {}
    mun_name = _safe_text(row.get("MUN_NAME"))
    mun_name_genitive = _mail_mun_name_genitive(mun_name, _safe_text(inflected.get("MUN_NAME_1")))
    return {
        "HEAD_FIO": _safe_text(row.get("HEAD_FIO")),
        "ADM_NAME": _safe_text(row.get("ADM_NAME")),
        "MUN_NAME": mun_name,
        "MUN_NAME_GENITIVE": mun_name_genitive,
        "MUN_R_NAME": mun_name_genitive,
        "DATE": datetime.now().strftime("%d.%m.%Y"),
        "OUTGOING_NUMBER": outgoing_number,
        "OUTGOING_NUMBER_KP": f"{outgoing_number}-КП" if outgoing_number else "",
    }


def _render_mail_template(template: str, row: dict[str, Any]) -> str:
    values = _mail_template_values(row)
    bracket_replacements = {
        "наименование муниципального образования": values["MUN_NAME"],
        "наименование муниципального образования в родительном падеже": values["MUN_NAME_GENITIVE"],
        "муниципальное образование в родительном падеже": values["MUN_NAME_GENITIVE"],
        "мун р": values["MUN_NAME_GENITIVE"],
        "номер": values["OUTGOING_NUMBER"],
        "номер кп": values["OUTGOING_NUMBER_KP"],
        "дата": values["DATE"],
    }

    rendered = template
    # Existing mail templates used the nominative placeholder after "проектирования",
    # where Russian grammar requires the genitive form.
    rendered = re.sub(
        r"(градостроительного\s+проектирования\s*)\[\s*наименование\s+муниципального\s+образования\s*\]",
        rf"\1{values['MUN_R_NAME']}",
        rendered,
        flags=re.IGNORECASE,
    )
    for placeholder, value in bracket_replacements.items():
        rendered = re.sub(
            rf"\[\s*{re.escape(placeholder)}\s*\]",
            value,
            rendered,
            flags=re.IGNORECASE,
        )

    return rendered.format_map(_SafeMailTemplateValues(values))


def _build_mail_footer_html(*, inline_image: bool = False) -> str:
    return MAIL_FOOTER_HTML_TEMPLATE.format(image_src=_mail_footer_image_src(inline=inline_image))


def _mail_footer_image_src(*, inline: bool = False) -> str:
    if inline:
        return f"cid:{MAIL_FOOTER_LOGO_CID}"
    signature_image_url = _safe_text(settings.mail_signature_image_url).rstrip("/")
    if signature_image_url:
        return signature_image_url
    public_base_url = _safe_text(settings.public_base_url).rstrip("/")
    if public_base_url:
        return f"{public_base_url}/public/mail-signature.png"
    return ""


def _append_mail_footer_text(body: str) -> str:
    body_text = body.rstrip()
    footer_text = DEFAULT_MAIL_FOOTER_TEXT.strip()
    if footer_text in body_text or "Черкашина Наталья Александровна" in body_text:
        return body_text
    body_text = re.sub(
        r"\n{2,}С уважением,\s*\nООО\s+«ПР»\s*$",
        "",
        body_text,
        flags=re.IGNORECASE,
    ).rstrip()
    return f"{body_text}\n\n{footer_text}" if body_text else footer_text


def _parse_emails(raw_value: Any) -> list[str]:
    raw_text = _safe_text(raw_value)
    if not raw_text:
        return []
    parts = re.split(r"[;,]", raw_text)
    result: list[str] = []
    for part in parts:
        email = part.strip()
        if email and email not in result:
            result.append(email)
    return result


def _is_valid_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.match(value))


def _choose_recipient(row: dict[str, Any]) -> dict[str, Any]:
    primary_candidates = _parse_emails(row.get("EMAIL_OSN"))
    extra_candidates = _parse_emails(row.get("EMAIL_DOP"))
    all_candidates = primary_candidates + [item for item in extra_candidates if item not in primary_candidates]
    valid_emails = [item for item in all_candidates if _is_valid_email(item)]
    invalid_emails = [item for item in all_candidates if item and item not in valid_emails]

    strategy = "no_valid_email"
    recipient = None
    fallback_candidates: list[str] = []
    decision_reason = "В строке не найдено ни одного валидного email."

    valid_primary = [item for item in primary_candidates if _is_valid_email(item)]
    valid_extra = [item for item in extra_candidates if _is_valid_email(item)]

    if valid_primary:
        recipient = valid_primary[0]
        fallback_candidates = [item for item in valid_emails if item != recipient]
        strategy = "primary"
        decision_reason = "Использую основной email из EMAIL_OSN."
    elif valid_extra:
        recipient = valid_extra[0]
        fallback_candidates = [item for item in valid_emails if item != recipient]
        strategy = "fallback_extra"
        if primary_candidates:
            decision_reason = "Основной email отсутствует или невалиден, использую EMAIL_DOP."
        else:
            decision_reason = "Основной email пустой, использую EMAIL_DOP."

    return {
        "recipient": recipient,
        "valid_emails": valid_emails,
        "invalid_emails": invalid_emails,
        "strategy": strategy,
        "fallback_candidates": fallback_candidates,
        "decision_reason": decision_reason,
        "needs_parser": recipient is None,
    }


def _allowed_send_recipients(email_decision: dict[str, Any]) -> list[str]:
    return list(email_decision.get("valid_emails") or [])


def _resolve_output_folder(row_id: Any, *, output_dir: Path | None = None) -> tuple[Path | None, str | None]:
    if row_id in (None, ""):
        return None, "Не указан ID строки."

    root_dir = output_dir or OUTPUT_DIR
    prefix = f"{row_id}_"
    matches = [path for path in root_dir.iterdir() if path.is_dir() and path.name.startswith(prefix)] if root_dir.exists() else []
    if not matches:
        return None, f"Не найдена папка output для ID={row_id}."
    if len(matches) > 1:
        return None, f"Найдено несколько папок output для ID={row_id}."
    return matches[0], None


def _resolve_pdf_attachments(folder: Path | None) -> tuple[list[str], str | None]:
    if folder is None:
        return [], "Папка документов не определена."

    pdf_files = sorted(folder.glob("*.pdf"))
    if len(pdf_files) < 2:
        return [str(path) for path in pdf_files], f"В папке {folder.name} найдено меньше двух PDF."
    return [str(path) for path in pdf_files], None


def _status_class(raw_status: Any) -> str:
    status_text = _safe_text(raw_status).upper()
    if not status_text:
        return "pending"
    if status_text in STATUS_OK_VALUES:
        return "sent"
    if "ОШИБ" in status_text or "ERROR" in status_text:
        return "error"
    return "other"


def _format_error_status(error: Any) -> str:
    error_text = re.sub(r"\s+", " ", _safe_text(error)).strip()
    if not error_text:
        return STATUS_ERROR_VALUE
    if len(error_text) > MAX_STATUS_ERROR_LENGTH:
        error_text = error_text[: MAX_STATUS_ERROR_LENGTH - 3].rstrip() + "..."
    return f"{STATUS_ERROR_VALUE}: {error_text}"


def _persist_row_status(
    workbook: Any,
    worksheet: Any,
    data_xlsx_path: Path,
    row: dict[str, Any],
    status_value: str,
    *,
    flush: bool = True,
) -> str:
    try:
        update_status(worksheet, row["_row_index"], status_value)
        row["STATUS"] = status_value
        if flush:
            save_workbook(workbook, data_xlsx_path)
        return ""
    except Exception as exc:
        return f"Не удалось сохранить статус строки в data.xlsx: {_safe_text(exc) or exc}"


def _wait_sender_delay(delay_seconds: float, state: dict[str, Any], job_id: str | None = None) -> bool:
    deadline = perf_counter() + max(0.0, delay_seconds)
    while perf_counter() < deadline:
        _refresh_sender_stop_flag(state, job_id)
        if state.get("stop_requested"):
            return False
        sleep(min(1.0, max(0.0, deadline - perf_counter())))
    return True


def _collect_excel_stats(data_xlsx_path: Path | None = None) -> dict[str, int]:
    source_path = data_xlsx_path or DATA_XLSX_PATH
    if not source_path.exists():
        return {"total": 0, "sent": 0, "error": 0, "pending": 0}

    _, _, rows = load_rows(source_path)
    stats = {"total": len(rows), "sent": 0, "error": 0, "pending": 0}
    for row in rows:
        status_class = _status_class(row.get("STATUS"))
        if status_class == "sent":
            stats["sent"] += 1
        elif status_class == "error":
            stats["error"] += 1
        else:
            stats["pending"] += 1
    return stats


def _format_sender_summary(state: dict[str, Any]) -> str:
    if state.get("status") == "stopped":
        summary = (
            f"Отправка через {state.get('transport') or 'smtp'} остановлена пользователем: обработано {state.get('processed_rows', 0)} строк, "
            f"успешно отправлено {state.get('sent_rows', 0)}, готово {state.get('ready_rows', 0)}, "
            f"ошибок {state.get('error_rows', 0)}."
        )
        if state.get("warning_rows", 0) > 0:
            summary += (
                f" У {state.get('warning_rows', 0)} строк письмо ушло, "
                "но копию не удалось сохранить в папку «Отправленные»."
            )
        if state.get("remaining_rows", 0) > 0:
            summary += f" Осталось строк без статуса «Отправлено»: {state.get('remaining_rows', 0)}."
        return summary
    if state.get("total_rows", 0) == 0:
        return "В data.xlsx пока нет строк для отправки."
    mode = "проверка готовности" if state.get("mode") == "dry_run" else "отправка"
    summary = (
        f"Завершена {mode} через {state.get('transport') or 'smtp'}: проверено {state.get('processed_rows', 0)} строк, "
        f"готово {state.get('ready_rows', 0)}, ошибок {state.get('error_rows', 0)}, "
        f"пропущено {state.get('skipped_rows', 0)}, уже отправлено {state.get('sent_rows', 0)}, "
        f"на дозаполнение отправлено {state.get('handoff_rows', 0)}."
    )
    if state.get("generator_handoff_rows", 0) > 0:
        summary += f" Генератору передано задач на восстановление документов: {state.get('generator_handoff_rows', 0)}."
    if state.get("philology_blocked_rows", 0) > 0:
        summary += f" Заблокировано замечаниями филолога: {state.get('philology_blocked_rows', 0)}."
    if state.get("autonomous_recovery_rows", 0) > 0:
        summary += f" Автономно восстановлено кейсов: {state.get('autonomous_recovery_rows', 0)}."
    if state.get("warning_rows", 0) > 0:
        summary += (
            f" У {state.get('warning_rows', 0)} строк письмо ушло, "
            "но копию не удалось сохранить в папку «Отправленные»."
        )
    if state.get("remaining_rows", 0) > 0:
        summary += f" После этой партии осталось строк без статуса «Отправлено»: {state.get('remaining_rows', 0)}."
    return summary


def _normalize_limit(limit: int | None, *, dry_run: bool) -> int | None:
    if limit in (None, 0):
        return None

    normalized = max(1, int(limit))
    return min(normalized, max(1, int(settings.sender_max_batch_size)))


def _normalize_transport(transport: str | None) -> str:
    value = _safe_text(transport).lower()
    if value in {"unisender", "smtp"}:
        return value
    configured = _safe_text(settings.sender_transport).lower()
    return configured if configured in {"unisender", "smtp"} else "smtp"


def request_sender_stop(job_id: str | None = None) -> dict[str, Any]:
    state = _load_sender_state(job_id)
    state["stop_requested"] = True
    state["stop_requested_at"] = datetime.now().isoformat(timespec="seconds")
    if state.get("status") == "running":
        state["summary_text"] = (
            "Получен запрос на остановку. Отправщик завершит текущую строку и больше не будет брать новые."
        )
    _save_sender_state(state, job_id)
    return get_sender_status(job_id)


def clear_sender_stop_request(job_id: str | None = None) -> None:
    state = _load_sender_state(job_id)
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _save_sender_state(state, job_id)


def _active_sender_review_task(row_id: Any, *, job_id: str | None = None) -> dict[str, Any] | None:
    if not settings.inter_agent_handoffs_enabled:
        return None
    row_id_text = _safe_text(row_id)
    for item in get_tasks_for_agent("sender", job_id):
        if _safe_text(item.get("task_type")) != "review_before_send":
            continue
        if _safe_text(item.get("row_id")) != row_id_text:
            continue
        if _safe_text(item.get("status")) not in {"pending", "in_progress"}:
            continue
        return item
    return None


def _retry_row_resources(
    row_id: Any,
    *,
    output_dir: Path | None = None,
) -> tuple[Path | None, str | None, list[str], str | None]:
    folder, folder_error = _resolve_output_folder(row_id, output_dir=output_dir)
    attachments, attachment_error = _resolve_pdf_attachments(folder)
    return folder, folder_error, attachments, attachment_error


def _delegate_sender_problem(
    *,
    symptom: str,
    row_id: Any,
    mun_name: str,
    details: dict[str, Any],
    job_id: str | None = None,
) -> dict[str, Any]:
    if not settings.inter_agent_handoffs_enabled:
        return {}
    diagnosis = diagnose_responsibility(symptom=symptom, context=details)
    task_type = {
        "missing_output": "generate_missing_documents",
        "missing_attachments": "restore_missing_attachments",
        "recipient_data_missing": "enrich_email",
        "review_before_send": "review_before_send",
    }.get(diagnosis["problem_type"], diagnosis["problem_type"])
    return create_task(
        source_agent="sender",
        target_agent=diagnosis["owner_agent"],
        owner_agent=diagnosis["owner_agent"],
        task_type=task_type,
        problem_type=diagnosis["problem_type"],
        symptom=symptom,
        root_cause=diagnosis["root_cause"],
        priority=diagnosis["priority"],
        blocking=diagnosis["blocking"],
        can_retry_after=diagnosis["can_retry_after"],
        row_id=row_id,
        mun_name=mun_name,
        details=details,
        job_id=job_id,
    )


def _run_autonomous_recovery_for_generator(*, row_id: Any, job_id: str | None = None) -> dict[str, Any]:
    row_id_text = _safe_text(row_id)
    generator_result = run_generator_agent(row_ids=[row_id_text] if row_id_text else None, job_id=job_id)
    result = {
        "generator_result": generator_result,
    }
    if settings.philologist_auto_run_enabled:
        result["philologist_result"] = run_philologist(
            ai_enabled=True,
            row_ids=[row_id_text] if row_id_text else None,
            job_id=job_id,
        )
    return result


def _run_autonomous_recovery_for_philologist(*, row_id: Any, job_id: str | None = None) -> dict[str, Any]:
    if not settings.philologist_auto_run_enabled:
        return {"philologist_result": {"status": "skipped", "summary_text": "Автозапуск филолога отключён."}}
    row_id_text = _safe_text(row_id)
    philologist_result = run_philologist(ai_enabled=True, row_ids=[row_id_text] if row_id_text else None, job_id=job_id)
    return {"philologist_result": philologist_result}


def preview_recipients(*, limit: int | None = None, job_id: str | None = None) -> dict[str, Any]:
    data_xlsx_path = _resolve_sender_data_xlsx_path(job_id)
    if not data_xlsx_path.exists():
        return {
            "status": "error",
            "summary_text": "Файл data.xlsx не найден.",
            "rows": [],
            "total_rows": 0,
        }

    _, _, rows = load_rows(data_xlsx_path)
    effective_limit = _normalize_limit(limit, dry_run=True)
    candidates = rows[:effective_limit] if effective_limit else rows
    preview_rows: list[dict[str, Any]] = []
    missing_count = 0
    fallback_count = 0
    invalid_count = 0
    extra_rows_count = 0
    extra_addresses_count = 0

    for row in candidates:
        email_decision = _choose_recipient(row)
        status_class = _status_class(row.get("STATUS"))
        primary_emails = _parse_emails(row.get("EMAIL_OSN"))
        extra_emails = _parse_emails(row.get("EMAIL_DOP"))
        entry = {
            "id": row.get("ID"),
            "mun_name": _safe_text(row.get("MUN_NAME")),
            "status_before": _safe_text(row.get("STATUS")),
            "status_class": status_class,
            "recipient": email_decision["recipient"],
            "email_strategy": email_decision["strategy"],
            "decision_reason": email_decision["decision_reason"],
            "primary_emails": primary_emails,
            "extra_emails": extra_emails,
            "invalid_emails": email_decision["invalid_emails"],
            "fallback_candidates": email_decision["fallback_candidates"],
        }
        if not entry["recipient"]:
            missing_count += 1
        if entry["email_strategy"] == "fallback_extra":
            fallback_count += 1
        if extra_emails:
            extra_rows_count += 1
            extra_addresses_count += len(extra_emails)
        if entry["invalid_emails"]:
            invalid_count += 1
        preview_rows.append(entry)

    summary_text = (
        f"Предпросмотр адресов: строк {len(preview_rows)}, "
        f"без адреса {missing_count}, "
        f"с дополнительными адресами {extra_rows_count}, "
        f"дополнительных адресов всего {extra_addresses_count}, "
        f"где EMAIL_DOP стал основным {fallback_count}, "
        f"с невалидными значениями {invalid_count}."
    )
    return {
        "status": "ok",
        "summary_text": summary_text,
        "rows": preview_rows,
        "total_rows": len(preview_rows),
        "effective_limit": effective_limit,
        "missing_count": missing_count,
        "fallback_count": fallback_count,
        "extra_rows_count": extra_rows_count,
        "extra_addresses_count": extra_addresses_count,
        "invalid_count": invalid_count,
    }


def _format_preview_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> str:
    if not rows:
        return "Подходящих строк для показа не нашлось."
    lines: list[str] = []
    for item in rows[:limit]:
        recipient = item.get("recipient") or "нет валидного адреса"
        strategy = item.get("email_strategy") or "unknown"
        lines.append(
            f"{item.get('id')} {item.get('mun_name')}: {recipient} "
            f"({item.get('decision_reason')}; стратегия: {strategy})"
        )
    return "\n".join(lines)


def _build_message(
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    *,
    mail_template_path: Path | None = None,
) -> EmailMessage:
    body = _build_mail_body(row, mail_template_path=mail_template_path)
    message = EmailMessage()
    message["From"] = settings.smtp_sender_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(
        _htmlify_mail_body(body, inline_footer_image=True, include_unsubscribe=False),
        subtype="html",
    )
    if MAIL_FOOTER_LOGO_PATH.exists():
        html_part = message.get_payload()[-1]
        html_part.add_related(
            MAIL_FOOTER_LOGO_PATH.read_bytes(),
            maintype="image",
            subtype="png",
            cid=f"<{MAIL_FOOTER_LOGO_CID}>",
            filename=MAIL_FOOTER_LOGO_PATH.name,
        )

    for attachment_path in attachments:
        path = Path(attachment_path)
        message.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=path.name,
        )
    return message


def _build_mail_body(row: dict[str, Any], *, mail_template_path: Path | None = None) -> str:
    body = _render_mail_template(_read_mail_template(mail_template_path), row)
    body = re.sub(
        r"(?im)^\s*Срок\s+действия\s+коммерческого\s+предложения\s*[—-]\s*до\s+31\.05\.2026\.\s*\n?",
        "",
        body,
    ).strip()
    return _append_mail_footer_text(body)


def _build_mail_subject(subject_template: str, row: dict[str, Any]) -> str:
    return _render_mail_template(_safe_text(subject_template) or DEFAULT_MAIL_SUBJECT, row).strip()


def _append_sent_mail_log(
    *,
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    transport: str,
    warning: str = "",
    provider: dict[str, Any] | None = None,
    sent_mail_log_path: Path | None = None,
) -> str | None:
    safe_provider = _safe_provider_payload(provider)
    record = {
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "transport": _safe_text(transport) or "smtp",
        "row_id": _safe_text(row.get("ID")),
        "mun_name": _safe_text(row.get("MUN_NAME")),
        "recipient": _safe_text(recipient),
        "subject": _safe_text(subject),
        "attachments": [Path(path).name for path in attachments if _safe_text(path)],
        "attachment_paths": [str(Path(path)) for path in attachments if _safe_text(path)],
        "warning": _safe_text(warning),
    }
    if safe_provider:
        record["provider"] = safe_provider
        if safe_provider.get("message_id"):
            record["provider_message_id"] = safe_provider["message_id"]
        if safe_provider.get("job_id"):
            record["provider_job_id"] = safe_provider["job_id"]
    try:
        log_path = sent_mail_log_path or SENT_MAIL_LOG_PATH
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return (
            "Письмо отправлено, но не удалось записать его в локальный журнал: "
            f"{_safe_text(exc) or 'ошибка записи файла'}."
        )
    return None


def _mail_key(value: Any) -> str:
    return _safe_text(value).lower()


def _load_sent_mail_recipients(sent_mail_log_path: Path | None = None) -> dict[str, set[str]]:
    log_path = sent_mail_log_path or SENT_MAIL_LOG_PATH
    sent_by_row: dict[str, set[str]] = {}
    if not log_path.exists():
        return sent_by_row

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return sent_by_row

    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        row_id = _safe_text(item.get("row_id"))
        recipient = _mail_key(item.get("recipient"))
        if not row_id or not recipient:
            continue
        sent_by_row.setdefault(row_id, set()).add(recipient)
    return sent_by_row


def _load_sent_mail_log_items(sent_mail_log_path: Path | None = None) -> list[dict[str, Any]]:
    log_path = sent_mail_log_path or SENT_MAIL_LOG_PATH
    if not log_path.exists():
        return []

    items: list[dict[str, Any]] = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _check_unisender_classic_messages(email_ids: list[str]) -> dict[str, dict[str, Any]]:
    api_key = _safe_text(settings.unisender_api_key)
    unique_ids = [_safe_text(item) for item in dict.fromkeys(email_ids) if _safe_text(item)]
    if not api_key or not unique_ids:
        return {}

    params = {
        "format": "json",
        "api_key": api_key,
        "email_id": ",".join(unique_ids[:500]),
    }
    request = Request(
        _build_unisender_classic_url(UNISENDER_CLASSIC_CHECK_PATH),
        data=urlencode(params).encode("utf-8", errors="ignore"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
    )
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"UniSender checkEmail вернул непонятный ответ: {raw[:300]}") from exc
    if data.get("error"):
        raise RuntimeError(_safe_text(data.get("error")) or "UniSender checkEmail вернул ошибку.")

    statuses = (data.get("result") or {}).get("statuses") if isinstance(data.get("result"), dict) else data.get("statuses")
    result: dict[str, dict[str, Any]] = {}
    if isinstance(statuses, list):
        for item in statuses:
            if not isinstance(item, dict):
                continue
            message_id = _safe_text(item.get("id"))
            if message_id:
                result[message_id] = {
                    "provider_status": _safe_text(item.get("status")),
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                }
    return result


def _unisender_status_label(status: str) -> str:
    normalized = _safe_text(status)
    labels = {
        "accepted": "принято UniSender в обработку",
        "success": "принято UniSender Go в обработку",
        "not_sent": "ещё не обработано",
        "ok_sent": "отправлено, ждём подтверждение доставки",
        "ok_delivered": "доставлено",
        "ok_read": "прочитано",
        "ok_link_visited": "перешли по ссылке",
        "ok_unsubscribed": "получатель отписался",
        "ok_spam_folder": "попало в спам",
        "err_user_unknown": "ошибка: неизвестный пользователь",
        "err_user_inactive": "ошибка: ящик неактивен",
        "err_mailbox_full": "ошибка: ящик переполнен",
        "err_spam_rejected": "ошибка: отклонено как спам",
        "err_delivery_failed": "ошибка доставки",
        "err_will_retry": "временная ошибка, UniSender повторит",
        "err_lost": "статус потерян, нужна проверка вручную",
    }
    return labels.get(normalized, normalized or "статус неизвестен")


def get_unisender_history(
    *,
    job_id: str | None = None,
    limit: int = 50,
    refresh: bool = False,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    sent_mail_log_path = None if job_paths.uses_legacy_layout else job_paths.sent_mail_log_path
    items = [
        item
        for item in _load_sent_mail_log_items(sent_mail_log_path)
        if _safe_text(item.get("transport")) == "unisender"
    ]
    items = list(reversed(items))[: max(1, min(int(limit or 50), 200))]

    classic_ids: list[str] = []
    for item in items:
        provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
        if _safe_text(provider.get("provider")) != "unisender_classic":
            continue
        message_id = _safe_text(item.get("provider_message_id") or provider.get("message_id"))
        if message_id:
            classic_ids.append(message_id)
    provider_statuses: dict[str, dict[str, Any]] = {}
    refresh_error = ""
    if refresh and classic_ids:
        try:
            provider_statuses = _check_unisender_classic_messages(classic_ids)
        except Exception as exc:
            refresh_error = _safe_text(exc) or "не удалось обновить статусы UniSender"

    result_items: list[dict[str, Any]] = []
    for item in items:
        provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
        provider_name = _safe_text(provider.get("provider")) or "unisender"
        message_id = _safe_text(item.get("provider_message_id") or provider.get("message_id"))
        job_provider_id = _safe_text(item.get("provider_job_id") or provider.get("job_id"))
        provider_status = _safe_text(provider.get("status")) or "accepted"
        checked_at = ""
        if message_id and message_id in provider_statuses:
            provider_status = provider_statuses[message_id].get("provider_status") or provider_status
            checked_at = provider_statuses[message_id].get("checked_at") or ""
        result_items.append(
            {
                "sent_at": item.get("sent_at"),
                "row_id": item.get("row_id"),
                "mun_name": item.get("mun_name"),
                "recipient": item.get("recipient"),
                "subject": item.get("subject"),
                "attachments": item.get("attachments") or [],
                "provider": provider_name,
                "provider_message_id": message_id,
                "provider_job_id": job_provider_id,
                "provider_status": provider_status,
                "provider_status_label": _unisender_status_label(provider_status),
                "checked_at": checked_at,
                "warning": item.get("warning") or "",
            }
        )

    summary = f"История UniSender: найдено {len(result_items)} писем."
    if refresh_error:
        summary += f" Статусы доставки не удалось обновить: {refresh_error}."
    elif refresh and classic_ids:
        summary += " Статусы доставки обновлены через UniSender checkEmail."
    elif refresh:
        summary += " Для этих писем нет classic email_id для проверки через checkEmail."
    return {
        "status": "ok",
        "summary_text": summary,
        "items": result_items,
        "refresh_error": refresh_error,
    }


def _save_sent_copy(message: EmailMessage) -> str | None:
    if not settings.smtp_save_sent_copy:
        return None
    if not settings.smtp_sender_email or not settings.smtp_sender_password:
        return "Не удалось сохранить копию письма: не заданы почтовые учётные данные."
    if not settings.imap_host:
        return "Не удалось сохранить копию письма: не указан IMAP host."

    def _imap_utf7_decode(value: str) -> str:
        def _decode_match(match: re.Match[str]) -> str:
            chunk = match.group(1)
            if chunk == "-":
                return "&"
            payload = chunk[:-1].replace(",", "/")
            padding = "=" * ((4 - len(payload) % 4) % 4)
            raw = base64.b64decode(payload + padding)
            return raw.decode("utf-16-be", errors="ignore")

        return re.sub(r"&([A-Za-z0-9+,]+-)", _decode_match, value)

    def _imap_utf7_encode(value: str) -> str:
        result: list[str] = []
        buffer = ""

        def _flush_buffer() -> None:
            nonlocal buffer
            if not buffer:
                return
            encoded = base64.b64encode(buffer.encode("utf-16-be")).decode("ascii").rstrip("=")
            result.append("&" + encoded.replace("/", ",") + "-")
            buffer = ""

        for char in value:
            code = ord(char)
            if 0x20 <= code <= 0x7E and char != "&":
                _flush_buffer()
                result.append(char)
            elif char == "&":
                _flush_buffer()
                result.append("&-")
            else:
                buffer += char
        _flush_buffer()
        return "".join(result)

    def _extract_imap_mailbox_name(line: bytes | str) -> tuple[str, str]:
        text = line.decode("ascii", errors="ignore") if isinstance(line, bytes) else str(line)
        match = re.match(r'^\((?P<flags>.*?)\)\s+"[^"]*"\s+(?P<name>.+)$', text.strip())
        if not match:
            return "", ""
        flags = (match.group("flags") or "").strip()
        name = (match.group("name") or "").strip()
        if name.startswith('"') and name.endswith('"') and len(name) >= 2:
            name = name[1:-1]
        return flags, _imap_utf7_decode(name)

    def _discover_imap_sent_folders(client: imaplib.IMAP4) -> list[str]:
        try:
            status, mailboxes = client.list()
        except Exception:
            return []
        if status != "OK" or not mailboxes:
            return []

        discovered: list[str] = []
        for item in mailboxes:
            flags, name = _extract_imap_mailbox_name(item)
            if not name:
                continue
            haystack = f"{flags} {name}".lower()
            if "\\sent" in haystack or "sent" in haystack or "отправ" in haystack:
                if name not in discovered:
                    discovered.append(name)
        return discovered

    tried_folders: list[str] = []
    try:
        if settings.imap_use_ssl:
            client = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        else:
            client = imaplib.IMAP4(settings.imap_host, settings.imap_port)
        try:
            client.login(settings.smtp_sender_email, settings.smtp_sender_password)
            payload = message.as_bytes()
            internal_date = imaplib.Time2Internaldate(datetime.now().timestamp())
            folder_candidates = [
                _safe_text(settings.imap_sent_folder) or "Отправленные",
                "Sent",
                "Sent Messages",
                *_discover_imap_sent_folders(client),
            ]
            for folder in folder_candidates:
                if folder in tried_folders:
                    continue
                tried_folders.append(folder)
                try:
                    status, _ = client.append(_imap_utf7_encode(folder), "\\Seen", internal_date, payload)
                except imaplib.IMAP4.error:
                    status = "NO"
                if status == "OK":
                    return None
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except Exception as exc:
        return f"Не удалось сохранить копию письма в 'Отправленные': {_safe_text(exc) or 'ошибка IMAP'}."

    return (
        "Не удалось сохранить копию письма в папку 'Отправленные'. "
        f"Пробовал папки: {', '.join(tried_folders)}."
    )


def _send_via_smtp(
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    *,
    mail_template_path: Path | None = None,
) -> str | None:
    if not settings.smtp_allow_real_send:
        raise RuntimeError(
            "Реальная SMTP-отправка запрещена настройкой smtp_allow_real_send. "
            "Сейчас доступен только dry-run режим."
        )
    if not settings.smtp_sender_email or not settings.smtp_sender_password:
        raise RuntimeError("Не настроены SMTP-учётные данные отправителя.")
    if not settings.smtp_host:
        raise RuntimeError("Не указан SMTP host.")

    message = _build_message(row, recipient, attachments, subject, mail_template_path=mail_template_path)

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.login(settings.smtp_sender_email, settings.smtp_sender_password)
            server.send_message(message)
        return _save_sent_copy(message)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        server.ehlo()
        if settings.smtp_use_starttls:
            server.starttls()
            server.ehlo()
        server.login(settings.smtp_sender_email, settings.smtp_sender_password)
        server.send_message(message)
    return _save_sent_copy(message)


def _htmlify_mail_body(
    body: str,
    *,
    inline_footer_image: bool = False,
    include_unsubscribe: bool = True,
) -> str:
    parts = [escape(line.strip()) for line in body.splitlines()]
    non_empty = [line for line in parts if line]
    html = "<br>".join(non_empty)
    if "Черкашина Наталья Александровна" in body:
        marker = "С уважением,<br>Черкашина Наталья Александровна"
        marker_index = html.find(marker)
        if marker_index >= 0:
            html = html[:marker_index] + _build_mail_footer_html(inline_image=inline_footer_image)
    if include_unsubscribe and "{{UnsubscribeUrl}}" not in html:
        html += "<br><br><a href='{{UnsubscribeUrl}}'>Отписаться от писем</a>"
    return html


def _build_unisender_go_url(path: str) -> str:
    base_url = _safe_text(settings.unisender_api_base_url).rstrip("/")
    if not base_url:
        base_url = "https://goapi.unisender.ru/ru/transactional/api/v1"
    return f"{base_url}/{path.lstrip('/')}"


def _uses_unisender_go_api() -> bool:
    base_url = _safe_text(settings.unisender_api_base_url).lower()
    return "goapi.unisender.ru" in base_url


def _build_unisender_classic_url(path: str) -> str:
    base_url = _safe_text(settings.unisender_api_base_url).rstrip("/")
    if not base_url:
        base_url = "https://api.unisender.com/ru/api"
    return f"{base_url}/{path.lstrip('/')}"


def _safe_provider_payload(provider: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(provider, dict):
        return {}
    allowed_keys = {
        "provider",
        "status",
        "message_id",
        "email_id",
        "job_id",
        "recipient",
        "accepted_emails",
        "failed_emails",
        "idempotence_key",
    }
    safe: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in provider:
            continue
        value = provider.get(key)
        if value in (None, "", [], {}):
            continue
        safe[key] = value
    return safe


def _send_via_unisender_classic(
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    *,
    mail_template_path: Path | None = None,
) -> dict[str, Any]:
    api_key = _safe_text(settings.unisender_api_key)
    sender_email = _safe_text(settings.unisender_sender_email or settings.smtp_sender_email)
    sender_name = _safe_text(settings.unisender_sender_name) or "ООО «ПР»"
    list_id = int(settings.unisender_list_id or 1)
    if not api_key:
        raise RuntimeError("Не указан API-ключ UniSender.")
    if not sender_email:
        raise RuntimeError("Не указан подтверждённый email отправителя UniSender.")

    body = _htmlify_mail_body(
        _build_mail_body(row, mail_template_path=mail_template_path),
        include_unsubscribe=False,
    )
    payload: dict[str, Any] = {
        "format": "json",
        "api_key": api_key,
        "email": recipient,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "subject": subject,
        "body": body,
        "list_id": list_id,
        "lang": "ru",
        "error_checking": 1,
    }

    for attachment_path in attachments:
        path = Path(attachment_path)
        payload[f"attachments[{path.name}]"] = path.read_bytes()

    request = Request(
        _build_unisender_classic_url(UNISENDER_CLASSIC_SEND_PATH),
        data=urlencode(payload).encode("utf-8", errors="ignore"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
    )
    try:
        raw = _run_unisender_request(request, timeout=60, request_label="UniSender")
    except HTTPError as exc:
        raw = getattr(exc, "raw_body", "")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"UniSender вернул HTTP {exc.code}: {raw[:300]}") from exc
        error_message = _safe_text(data.get("error"))
        if error_message:
            raise RuntimeError(error_message) from exc
        raise RuntimeError(f"UniSender вернул HTTP {exc.code}: {raw[:300]}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"UniSender вернул непонятный ответ: {raw[:300]}") from exc

    result_items = data.get("result")
    if isinstance(result_items, list) and result_items:
        first = result_items[0]
        errors = first.get("errors") or []
        if errors:
            message = "; ".join(_safe_text(err.get("message")) for err in errors if isinstance(err, dict))
            raise RuntimeError(message or "UniSender отклонил письмо.")
        if first.get("id"):
            message_id = _safe_text(first.get("id"))
            return {
                "provider": "unisender_classic",
                "status": "accepted",
                "message_id": message_id,
                "email_id": message_id,
                "recipient": _safe_text(first.get("email")) or recipient,
            }

    legacy_result = data.get("result")
    if isinstance(legacy_result, dict) and legacy_result.get("email_id"):
        message_id = _safe_text(legacy_result.get("email_id"))
        return {
            "provider": "unisender_classic",
            "status": "accepted",
            "message_id": message_id,
            "email_id": message_id,
            "recipient": recipient,
        }

    if data.get("error"):
        raise RuntimeError(_safe_text(data.get("error")) or "UniSender вернул ошибку.")
    raise RuntimeError("UniSender не подтвердил отправку письма.")


def _send_via_unisender(
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    *,
    mail_template_path: Path | None = None,
) -> dict[str, Any]:
    if not _uses_unisender_go_api():
        return _send_via_unisender_classic(
            row,
            recipient,
            attachments,
            subject,
            mail_template_path=mail_template_path,
        )

    api_key = _safe_text(settings.unisender_api_key)
    sender_email = _safe_text(settings.unisender_sender_email or settings.smtp_sender_email)
    sender_name = _safe_text(settings.unisender_sender_name) or "ООО «ПР»"
    if not api_key:
        raise RuntimeError("Не указан API-ключ UniSender Go.")
    if not sender_email:
        raise RuntimeError("Не указан email отправителя UniSender Go.")

    plaintext_body = _build_mail_body(row, mail_template_path=mail_template_path)
    html_body = _htmlify_mail_body(plaintext_body, include_unsubscribe=False)
    idempotence_key = secrets.token_urlsafe(24)
    payload: dict[str, Any] = {
        "message": {
            "recipients": [{"email": recipient}],
            "body": {
                "html": html_body,
                "plaintext": plaintext_body,
            },
            "subject": subject,
            "from_email": sender_email,
            "from_name": sender_name,
            "reply_to": sender_email,
            "reply_to_name": sender_name,
            "global_language": "ru",
            "template_engine": "simple",
            "track_links": 1,
            "track_read": 1,
            "idempotence_key": idempotence_key,
        }
    }

    encoded_attachments: list[dict[str, str]] = []
    for attachment_path in attachments:
        path = Path(attachment_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded_attachments.append(
            {
                "type": mime_type,
                "name": path.name,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
    if encoded_attachments:
        payload["message"]["attachments"] = encoded_attachments

    request = Request(
        _build_unisender_go_url(UNISENDER_GO_SEND_PATH),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": api_key,
        },
    )
    try:
        raw = _run_unisender_request(request, timeout=60, request_label="UniSender Go")
    except HTTPError as exc:
        raw = getattr(exc, "raw_body", "")
        try:
            data = json.loads(raw)
            message = _safe_text(data.get("message"))
            code = data.get("code")
            if message:
                suffix = f" (code {code})" if code is not None else ""
                raise RuntimeError(message + suffix) from exc
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"UniSender Go вернул HTTP {exc.code}: {raw[:300]}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"UniSender Go вернул непонятный ответ: {raw[:300]}") from exc

    if _safe_text(data.get("status")).lower() != "success":
        raise RuntimeError(_safe_text(data.get("message")) or "UniSender Go не подтвердил отправку письма.")

    failed_emails = data.get("failed_emails") or {}
    if isinstance(failed_emails, dict) and recipient in failed_emails:
        raise RuntimeError(f"UniSender Go не принял адрес {recipient}: {_safe_text(failed_emails.get(recipient))}")

    accepted_emails = data.get("emails") or []
    if accepted_emails and recipient not in accepted_emails:
        raise RuntimeError("UniSender Go не подтвердил адрес получателя в ответе.")
    return {
        "provider": "unisender_go",
        "status": _safe_text(data.get("status")) or "success",
        "job_id": _safe_text(data.get("job_id") or data.get("id")),
        "recipient": recipient,
        "accepted_emails": accepted_emails,
        "failed_emails": failed_emails if isinstance(failed_emails, dict) else {},
        "idempotence_key": idempotence_key,
    }


def _send_with_transport(
    row: dict[str, Any],
    recipients: list[str],
    attachments: list[str],
    subject: str,
    *,
    transport: str,
    mail_template_path: Path | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    sent_recipients: list[str] = []
    warnings: list[str] = []
    for recipient in recipients:
        try:
            warning = None
            provider: dict[str, Any] = {}
            if transport == "unisender":
                provider = _send_via_unisender(
                    row,
                    recipient,
                    attachments,
                    subject,
                    mail_template_path=mail_template_path,
                )
            else:
                warning = _send_via_smtp(
                    row,
                    recipient,
                    attachments,
                    subject,
                    mail_template_path=mail_template_path,
                )
        except Exception as exc:
            attempts.append({"recipient": recipient, "status": "error", "error": _safe_text(exc) or "SMTP error"})
            continue
        attempt: dict[str, Any] = {"recipient": recipient, "status": "sent", "error": ""}
        safe_provider = _safe_provider_payload(provider)
        if safe_provider:
            attempt["provider"] = safe_provider
            if safe_provider.get("message_id"):
                attempt["provider_message_id"] = safe_provider["message_id"]
            if safe_provider.get("job_id"):
                attempt["provider_job_id"] = safe_provider["job_id"]
        attempts.append(attempt)
        sent_recipients.append(recipient)
        if warning:
            warnings.append(warning)
    if sent_recipients and len(sent_recipients) == len(recipients):
        return {
            "recipient": sent_recipients[0],
            "recipients": sent_recipients,
            "attempts": attempts,
            "error": "",
            "warning": " ".join(warnings).strip(),
        }
    failed_errors = [attempt["error"] for attempt in attempts if attempt.get("status") == "error" and attempt.get("error")]
    last_error = "; ".join(failed_errors) or "Не найден получатель для отправки."
    return {
        "recipient": None,
        "recipients": sent_recipients,
        "attempts": attempts,
        "error": last_error,
        "warning": " ".join(warnings).strip(),
    }


def _provider_for_recipient(attempts: list[dict[str, Any]], recipient: str) -> dict[str, Any]:
    recipient_key = _mail_key(recipient)
    for attempt in attempts:
        if _mail_key(attempt.get("recipient")) != recipient_key:
            continue
        provider = attempt.get("provider")
        return _safe_provider_payload(provider if isinstance(provider, dict) else None)
    return {}


def _unisender_parallel_workers(*, dry_run: bool, transport: str) -> int:
    if dry_run or transport != "unisender":
        return 1
    return 1


def _build_parallel_send_job(
    *,
    row: dict[str, Any],
    entry: dict[str, Any],
    email_decision: dict[str, Any],
    recipients_to_send: list[str],
    attachments: list[str],
    subject: str,
    transport: str,
    mail_template_path: Path | None,
) -> dict[str, Any]:
    return {
        "row": row,
        "entry": entry,
        "email_decision": email_decision,
        "recipients_to_send": recipients_to_send,
        "attachments": attachments,
        "subject": subject,
        "transport": transport,
        "mail_template_path": mail_template_path,
    }


def _run_parallel_send_job(job: dict[str, Any]) -> dict[str, Any]:
    send_result = _send_with_transport(
        job["row"],
        job["recipients_to_send"],
        job["attachments"],
        job["subject"],
        transport=job["transport"],
        mail_template_path=job["mail_template_path"],
    )
    return {"job": job, "send_result": send_result}


def _restore_sent_from_local_log(
    *,
    entry: dict[str, Any],
    intended_recipients: list[str],
    row: dict[str, Any],
    workbook: Any,
    worksheet: Any,
    data_xlsx_path: Path,
) -> bool:
    entry["result"] = "skipped_logged_sent"
    entry["attempts"] = [
        {
            "recipient": recipient,
            "status": "already_sent",
            "error": "",
        }
        for recipient in intended_recipients
    ]
    entry["sent_recipients"] = intended_recipients
    entry["recipient"] = intended_recipients[0] if intended_recipients else entry["recipient"]
    entry["decision_reason"] = "Статус восстановлен по локальному журналу: письмо уже было отправлено ранее."
    status_warning = _persist_row_status(
        workbook,
        worksheet,
        data_xlsx_path,
        row,
        STATUS_SENT_VALUE,
        flush=False,
    )
    if status_warning:
        entry["warning"] = f"{entry['warning']} {status_warning}".strip()
        entry["next_action"] = f"{entry['next_action']} {status_warning}".strip()
        return False
    return True


def _apply_send_result_to_entry(
    *,
    entry: dict[str, Any],
    send_result: dict[str, Any],
    row: dict[str, Any],
    email_decision: dict[str, Any],
    attachments: list[str],
    row_subject: str,
    effective_transport: str,
    sent_mail_log_path: Path | None,
    sent_mail_recipients: dict[str, set[str]],
    workbook: Any,
    worksheet: Any,
    data_xlsx_path: Path,
) -> bool:
    entry["attempts"] = send_result["attempts"]
    entry["warning"] = _safe_text(send_result.get("warning"))
    partial_recipients = send_result.get("recipients") or []
    row_id_text = _safe_text(row.get("ID"))

    if partial_recipients and not send_result["recipient"]:
        for sent_recipient in partial_recipients:
            log_warning = _append_sent_mail_log(
                row=row,
                recipient=sent_recipient,
                attachments=attachments,
                subject=row_subject,
                transport=effective_transport,
                warning=entry["warning"],
                provider=_provider_for_recipient(entry["attempts"], sent_recipient),
                sent_mail_log_path=sent_mail_log_path,
            )
            sent_mail_recipients.setdefault(row_id_text, set()).add(_mail_key(sent_recipient))
            if log_warning:
                entry["warning"] = f"{entry['warning']} {log_warning}".strip() if entry["warning"] else log_warning

    if not send_result["recipient"]:
        raise RuntimeError(send_result["error"])

    entry["recipient"] = send_result["recipient"]
    entry["sent_recipients"] = send_result.get("recipients") or [entry["recipient"]]
    if len(entry["sent_recipients"]) > 1:
        entry["decision_reason"] = "Письмо отправлено на основной и дополнительный email."
    elif entry["email_strategy"] == "fallback_extra" or entry["recipient"] != email_decision["recipient"]:
        entry["decision_reason"] = "Письмо отправлено по резервному email после выбора лучшего доступного адреса."

    for sent_recipient in entry["sent_recipients"]:
        log_warning = _append_sent_mail_log(
            row=row,
            recipient=sent_recipient,
            attachments=attachments,
            subject=row_subject,
            transport=effective_transport,
            warning=entry["warning"],
            provider=_provider_for_recipient(entry["attempts"], sent_recipient),
            sent_mail_log_path=sent_mail_log_path,
        )
        if log_warning:
            entry["warning"] = f"{entry['warning']} {log_warning}".strip() if entry["warning"] else log_warning
        sent_mail_recipients.setdefault(row_id_text, set()).add(_mail_key(sent_recipient))

    status_warning = _persist_row_status(
        workbook,
        worksheet,
        data_xlsx_path,
        row,
        STATUS_SENT_VALUE,
        flush=False,
    )
    if status_warning:
        entry["warning"] = f"{entry['warning']} {status_warning}".strip()
        entry["next_action"] = f"{entry['next_action']} {status_warning}".strip()
        return False
    return True


def run_sender(
    *,
    dry_run: bool = True,
    limit: int | None = None,
    auto_recover: bool = False,
    row_ids: list[str] | None = None,
    transport: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    data_xlsx_path = _resolve_sender_data_xlsx_path(job_id)
    output_dir = None if job_paths.uses_legacy_layout else job_paths.output_dir
    job_template_docx_path = job_paths.templates_dir / "mail_template.docx"
    job_template_txt_path = job_paths.templates_dir / "mail_template.txt"
    if job_paths.uses_legacy_layout:
        mail_template_path = None
    elif job_template_docx_path.exists():
        mail_template_path = job_template_docx_path
    elif job_template_txt_path.exists():
        mail_template_path = job_template_txt_path
    else:
        mail_template_path = None
    sent_mail_log_path = None if job_paths.uses_legacy_layout else job_paths.sent_mail_log_path
    state = _load_sender_state(job_id)
    clear_sender_stop_request(job_id)
    state = _load_sender_state(job_id)
    effective_limit = _normalize_limit(limit, dry_run=dry_run)
    effective_transport = _normalize_transport(transport)
    stats = _collect_excel_stats(data_xlsx_path)
    state.update(
        {
            "status": "running",
            "mode": "dry_run" if dry_run else "send",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "processed_rows": 0,
            "ready_rows": 0,
            "sent_rows": stats["sent"],
            "error_rows": 0,
            "skipped_rows": 0,
            "total_rows": 0,
            "summary_text": "Агент-отправщик начал обработку строк.",
            "rows": [],
            "stats": stats,
            "warning_rows": 0,
            "handoff_rows": 0,
            "task_stats": count_tasks_for_agent("sender", job_id),
            "tasks": get_tasks_for_agent("sender", job_id)[:20],
            "recent_events": get_recent_events(agent_name="sender", limit=20, job_id=job_id),
            "generator_handoff_rows": 0,
            "philology_blocked_rows": 0,
            "autonomous_recovery_rows": 0,
            "effective_limit": effective_limit,
            "remaining_rows": 0,
            "stop_requested": False,
            "stop_requested_at": None,
            "transport": effective_transport,
        }
    )
    _save_sender_state(state, job_id)

    if not data_xlsx_path.exists():
        state["status"] = "error"
        state["summary_text"] = "Файл data.xlsx не найден."
        _save_sender_state(state, job_id)
        return dict(state)

    workbook, worksheet, rows = load_rows(data_xlsx_path)
    requested_row_ids = {str(item).strip() for item in (row_ids or []) if str(item).strip()}
    if requested_row_ids:
        rows = [row for row in rows if str(row.get("ID")).strip() in requested_row_ids]
    candidates = rows[:effective_limit] if effective_limit else rows
    state["total_rows"] = len(candidates)
    _save_sender_state(state, job_id)

    processed_entries: list[dict[str, Any]] = []
    runtime_warnings: list[str] = []
    workbook_dirty = False
    started_at = perf_counter()
    subject = DEFAULT_MAIL_SUBJECT
    sent_mail_recipients = _load_sent_mail_recipients(sent_mail_log_path) if not dry_run else {}
    parallel_workers = _unisender_parallel_workers(dry_run=dry_run, transport=effective_transport)
    parallel_send_jobs: list[dict[str, Any]] = []

    for row in candidates:
        _refresh_sender_stop_flag(state, job_id)
        if state.get("stop_requested"):
            break
        row_id = row.get("ID")
        row_status = _safe_text(row.get("STATUS"))
        entry: dict[str, Any] = {
            "id": row_id,
            "row_index": row.get("_row_index"),
            "mun_name": _safe_text(row.get("MUN_NAME")),
            "status_before": row_status,
            "result": "",
            "recipient": None,
            "emails": [],
            "invalid_emails": [],
            "email_strategy": "",
            "decision_reason": "",
            "fallback_candidates": [],
            "folder": None,
            "attachments": [],
            "error": "",
            "warning": "",
            "next_action": "",
            "attempts": [],
        }

        status_class = _status_class(row_status)
        if status_class == "sent":
            entry["result"] = "skipped_duplicate"
            entry["decision_reason"] = "Строка уже помечена как успешно отправленная."
            processed_entries.append(entry)
            state["skipped_rows"] += 1
            state["processed_rows"] += 1
            state["rows"] = _state_rows_snapshot(processed_entries)
            _save_sender_state(state, job_id)
            continue

        email_decision = _choose_recipient(row)
        entry["recipient"] = email_decision["recipient"]
        entry["emails"] = email_decision["valid_emails"]
        entry["invalid_emails"] = email_decision["invalid_emails"]
        entry["email_strategy"] = email_decision["strategy"]
        entry["decision_reason"] = email_decision["decision_reason"]
        entry["fallback_candidates"] = email_decision["fallback_candidates"]

        folder, folder_error = _resolve_output_folder(row_id, output_dir=output_dir)
        entry["folder"] = str(folder) if folder else None
        attachments, attachment_error = _resolve_pdf_attachments(folder)
        entry["attachments"] = attachments
        review_task = _active_sender_review_task(row_id, job_id=job_id)
        recovery_info: dict[str, Any] | None = None

        if not entry["recipient"]:
            entry["result"] = "needs_enrichment"
            entry["error"] = "Не найден валидный email получателя."
            entry["next_action"] = (
                "Нужно вручную проверить и заполнить email получателя."
                if not settings.inter_agent_handoffs_enabled
                else "Запросить у агента-парсера поиск или уточнение email."
            )
            task = _delegate_sender_problem(
                symptom="missing_recipient_data",
                row_id=row_id,
                mun_name=entry["mun_name"],
                details={
                    "email_osn": _safe_text(row.get("EMAIL_OSN")),
                    "email_dop": _safe_text(row.get("EMAIL_DOP")),
                    "reason": entry["error"],
                    "has_primary_email": bool(_parse_emails(row.get("EMAIL_OSN"))),
                    "has_any_valid_email": bool(email_decision["valid_emails"]),
                },
                job_id=job_id,
            )
            if task:
                entry["handoff_task_id"] = task.get("id")
                state["handoff_rows"] += 1
        elif folder_error:
            entry["result"] = "error_missing_output"
            entry["error"] = folder_error
            entry["next_action"] = (
                "Нужно вручную перезапустить генератор и пересобрать комплект документов."
                if not settings.inter_agent_handoffs_enabled
                else "Передать генератору задачу на пересборку комплекта документов."
            )
            task = _delegate_sender_problem(
                symptom="missing_output",
                row_id=row_id,
                mun_name=entry["mun_name"],
                details={
                    "reason": folder_error,
                    "folder_exists": False,
                    "attachment_count": 0,
                },
                job_id=job_id,
            )
            if task:
                entry["handoff_task_id"] = task.get("id")
                state["generator_handoff_rows"] += 1
            if auto_recover:
                recovery_info = _run_autonomous_recovery_for_generator(row_id=row_id, job_id=job_id)
                folder, folder_error, attachments, attachment_error = _retry_row_resources(
                    row_id,
                    output_dir=output_dir,
                )
                entry["folder"] = str(folder) if folder else None
                entry["attachments"] = attachments
                if not folder_error and not attachment_error and entry["recipient"]:
                    entry["result"] = "ready_after_recovery" if dry_run else "sent"
                    entry["decision_reason"] += " Генератор автоматически пересобрал комплект документов."
                    state["autonomous_recovery_rows"] += 1
        elif attachment_error:
            entry["result"] = "error_missing_attachments"
            entry["error"] = attachment_error
            entry["next_action"] = (
                "Нужно вручную перезапустить генератор и восстановить вложения."
                if not settings.inter_agent_handoffs_enabled
                else "Передать генератору задачу на восстановление вложений."
            )
            task = _delegate_sender_problem(
                symptom="missing_attachments",
                row_id=row_id,
                mun_name=entry["mun_name"],
                details={
                    "reason": attachment_error,
                    "folder": entry["folder"],
                    "folder_exists": bool(folder),
                    "attachment_count": len(attachments),
                },
                job_id=job_id,
            )
            if task:
                entry["handoff_task_id"] = task.get("id")
                state["generator_handoff_rows"] += 1
            if auto_recover:
                recovery_info = _run_autonomous_recovery_for_generator(row_id=row_id, job_id=job_id)
                folder, folder_error, attachments, attachment_error = _retry_row_resources(
                    row_id,
                    output_dir=output_dir,
                )
                entry["folder"] = str(folder) if folder else None
                entry["attachments"] = attachments
                if not folder_error and not attachment_error and entry["recipient"]:
                    entry["result"] = "ready_after_recovery" if dry_run else "sent"
                    entry["decision_reason"] += " Генератор автоматически восстановил вложения."
                    state["autonomous_recovery_rows"] += 1
        elif review_task:
            entry["result"] = "blocked_by_philologist"
            entry["error"] = _safe_text(review_task.get("details", {}).get("note")) or (
                "Перед отправкой нужно учесть замечания филолога."
            )
            diagnosis = diagnose_responsibility(
                symptom="philology_review_block",
                context={
                    "unresolved_issue_count": review_task.get("details", {}).get("unresolved_issue_count", 1),
                },
            )
            entry["next_action"] = (
                "Сначала завершить языковую проверку и согласовать замечания филолога. "
                + diagnosis["root_cause"]
            )
            state["philology_blocked_rows"] += 1
            if auto_recover and settings.philologist_auto_run_enabled:
                recovery_info = _run_autonomous_recovery_for_philologist(row_id=row_id, job_id=job_id)
                review_task = _active_sender_review_task(row_id, job_id=job_id)
                if not review_task:
                    entry["result"] = "ready_after_recovery" if dry_run else "sent"
                    entry["error"] = ""
                    entry["next_action"] = ""
                    entry["decision_reason"] += " Филолог автоматически перепроверил документы и снял блокер."
                    state["autonomous_recovery_rows"] += 1
            elif auto_recover:
                entry["next_action"] = (
                    "Нужна ручная проверка филолога. Автоматический запуск филолога сейчас отключён."
                )
        else:
            entry["result"] = "ready" if dry_run else "sent"

        if recovery_info:
            entry["recovery"] = {
                key: {
                    "summary_text": value.get("summary_text"),
                    "status": value.get("status"),
                }
                for key, value in recovery_info.items()
                if isinstance(value, dict)
            }

        if entry["result"] in {
            "needs_enrichment",
            "error_missing_output",
            "error_missing_attachments",
            "blocked_by_philologist",
            "error",
        }:
            if not dry_run:
                status_warning = _persist_row_status(
                    workbook,
                    worksheet,
                    data_xlsx_path,
                    row,
                    _format_error_status(entry["error"] or entry["result"]),
                    flush=False,
                )
                workbook_dirty = workbook_dirty or not bool(status_warning)
                if status_warning:
                    entry["warning"] = f"{entry['warning']} {status_warning}".strip()
            state["error_rows"] += 1
            processed_entries.append(entry)
            state["processed_rows"] += 1
            if _should_flush_sender_workbook(
                dirty=workbook_dirty,
                processed_rows=state["processed_rows"],
                total_rows=state["total_rows"],
            ):
                flush_warning = _flush_sender_workbook(workbook, data_xlsx_path)
                if flush_warning:
                    entry["warning"] = f"{entry['warning']} {flush_warning}".strip()
                    entry["next_action"] = f"{entry['next_action']} {flush_warning}".strip()
                    runtime_warnings.append(flush_warning)
                else:
                    workbook_dirty = False
            state["rows"] = _state_rows_snapshot(processed_entries)
            _save_sender_state(state, job_id)
            continue

        state["ready_rows"] += 1
        row_subject = _build_mail_subject(subject, row)

        if not dry_run:
            _refresh_sender_stop_flag(state, job_id)
            if state.get("stop_requested"):
                entry["result"] = "stopped_before_send"
                entry["next_action"] = "Отправка этой и следующих строк остановлена по запросу пользователя."
                state["ready_rows"] -= 1
                processed_entries.append(entry)
                state["processed_rows"] += 1
                state["rows"] = _state_rows_snapshot(processed_entries)
                _save_sender_state(state, job_id)
                break
            try:
                row_id_text = _safe_text(row_id)
                already_logged = sent_mail_recipients.get(row_id_text, set())
                intended_recipients = _allowed_send_recipients(email_decision)
                recipients_to_send = [
                    recipient
                    for recipient in intended_recipients
                    if _mail_key(recipient) not in already_logged
                ]
                if not recipients_to_send:
                    workbook_dirty = (
                        _restore_sent_from_local_log(
                            entry=entry,
                            intended_recipients=intended_recipients,
                            row=row,
                            workbook=workbook,
                            worksheet=worksheet,
                            data_xlsx_path=data_xlsx_path,
                        )
                        or workbook_dirty
                    )
                    state["sent_rows"] += 1
                elif parallel_workers > 1:
                    parallel_send_jobs.append(
                        _build_parallel_send_job(
                            row=row,
                            entry=entry,
                            email_decision=email_decision,
                            recipients_to_send=recipients_to_send,
                            attachments=attachments,
                            subject=row_subject,
                            transport=effective_transport,
                            mail_template_path=mail_template_path,
                        )
                    )
                    entry["result"] = "queued_parallel_send"
                    entry["next_action"] = "Письмо поставлено в очередь на параллельную отправку через UniSender."
                else:
                    send_result = _send_with_transport(
                        row,
                        recipients_to_send,
                        attachments,
                        row_subject,
                        transport=effective_transport,
                        mail_template_path=mail_template_path,
                    )
                    workbook_dirty = (
                        _apply_send_result_to_entry(
                            entry=entry,
                            send_result=send_result,
                            row=row,
                            email_decision=email_decision,
                            attachments=attachments,
                            row_subject=row_subject,
                            effective_transport=effective_transport,
                            sent_mail_log_path=sent_mail_log_path,
                            sent_mail_recipients=sent_mail_recipients,
                            workbook=workbook,
                            worksheet=worksheet,
                            data_xlsx_path=data_xlsx_path,
                        )
                        or workbook_dirty
                    )
                    state["sent_rows"] += 1
                if entry["warning"]:
                    state["warning_rows"] += 1
                    entry["next_action"] = entry["warning"]
            except Exception as exc:
                entry["result"] = "error_send"
                entry["error"] = _safe_text(exc) or "Ошибка SMTP-отправки."
                entry["next_action"] = "Повторить отправку позже или передать строку на ручную проверку."
                state["ready_rows"] -= 1
                state["error_rows"] += 1
                status_warning = _persist_row_status(
                    workbook,
                    worksheet,
                    data_xlsx_path,
                    row,
                    _format_error_status(entry["error"]),
                    flush=False,
                )
                workbook_dirty = workbook_dirty or not bool(status_warning)
                if status_warning:
                    entry["warning"] = f"{entry['warning']} {status_warning}".strip()
                    entry["next_action"] = f"{entry['next_action']} {status_warning}".strip()
        else:
            entry["attempts"] = [
                {
                    "recipient": entry["recipient"],
                    "status": "ready",
                    "error": "",
                }
            ]

        if entry.get("result") == "queued_parallel_send":
            state["rows"] = _state_rows_snapshot(processed_entries)
            _save_sender_state(state, job_id)
            continue

        processed_entries.append(entry)
        state["processed_rows"] += 1
        if _should_flush_sender_workbook(
            dirty=workbook_dirty,
            processed_rows=state["processed_rows"],
            total_rows=state["total_rows"],
        ):
            flush_warning = _flush_sender_workbook(workbook, data_xlsx_path)
            if flush_warning:
                entry["warning"] = f"{entry['warning']} {flush_warning}".strip()
                entry["next_action"] = f"{entry['next_action']} {flush_warning}".strip()
                runtime_warnings.append(flush_warning)
            else:
                workbook_dirty = False
        state["rows"] = _state_rows_snapshot(processed_entries)
        _save_sender_state(state, job_id)

        if not dry_run and entry.get("result") == "sent" and effective_transport != "unisender":
            delay_seconds = max(0.0, float(settings.sender_delay_seconds or 0))
            if delay_seconds > 0 and state["processed_rows"] < state["total_rows"]:
                if not _wait_sender_delay(delay_seconds, state, job_id):
                    state["summary_text"] = (
                        "Отправка остановлена пользователем во время паузы между письмами."
                    )
                    _save_sender_state(state, job_id)
                    break

    if parallel_send_jobs and not dry_run:
        max_workers = parallel_workers
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="unisender-send") as executor:
            pending_futures: dict[Future, dict[str, Any]] = {}
            next_job_index = 0
            stop_submitting = False

            while next_job_index < len(parallel_send_jobs) and len(pending_futures) < max_workers:
                job = parallel_send_jobs[next_job_index]
                pending_futures[executor.submit(_run_parallel_send_job, job)] = job
                next_job_index += 1

            while pending_futures:
                _refresh_sender_stop_flag(state, job_id)
                if state.get("stop_requested"):
                    stop_submitting = True
                done_futures, _ = wait(list(pending_futures.keys()), return_when=FIRST_COMPLETED)
                for future in done_futures:
                    job = pending_futures.pop(future)
                    entry = job["entry"]
                    row = job["row"]
                    try:
                        outcome = future.result()
                        send_result = outcome["send_result"]
                        workbook_dirty = (
                            _apply_send_result_to_entry(
                                entry=entry,
                                send_result=send_result,
                                row=row,
                                email_decision=job["email_decision"],
                                attachments=job["attachments"],
                                row_subject=job["subject"],
                                effective_transport=effective_transport,
                                sent_mail_log_path=sent_mail_log_path,
                                sent_mail_recipients=sent_mail_recipients,
                                workbook=workbook,
                                worksheet=worksheet,
                                data_xlsx_path=data_xlsx_path,
                            )
                            or workbook_dirty
                        )
                        state["sent_rows"] += 1
                        if entry["warning"]:
                            state["warning_rows"] += 1
                            entry["next_action"] = entry["warning"]
                        entry["result"] = "sent"
                    except Exception as exc:
                        entry["result"] = "error_send"
                        entry["error"] = _safe_text(exc) or "Ошибка UniSender-отправки."
                        entry["next_action"] = "Повторить отправку позже или передать строку на ручную проверку."
                        state["ready_rows"] -= 1
                        state["error_rows"] += 1
                        status_warning = _persist_row_status(
                            workbook,
                            worksheet,
                            data_xlsx_path,
                            row,
                            _format_error_status(entry["error"]),
                            flush=False,
                        )
                        workbook_dirty = workbook_dirty or not bool(status_warning)
                        if status_warning:
                            entry["warning"] = f"{entry['warning']} {status_warning}".strip()
                            entry["next_action"] = f"{entry['next_action']} {status_warning}".strip()

                    processed_entries.append(entry)
                    state["processed_rows"] += 1
                    if _should_flush_sender_workbook(
                        dirty=workbook_dirty,
                        processed_rows=state["processed_rows"],
                        total_rows=state["total_rows"],
                    ):
                        flush_warning = _flush_sender_workbook(workbook, data_xlsx_path)
                        if flush_warning:
                            entry["warning"] = f"{entry['warning']} {flush_warning}".strip()
                            entry["next_action"] = f"{entry['next_action']} {flush_warning}".strip()
                            runtime_warnings.append(flush_warning)
                        else:
                            workbook_dirty = False
                    state["rows"] = _state_rows_snapshot(processed_entries)
                    _save_sender_state(state, job_id)

                while not stop_submitting and next_job_index < len(parallel_send_jobs) and len(pending_futures) < max_workers:
                    job = parallel_send_jobs[next_job_index]
                    pending_futures[executor.submit(_run_parallel_send_job, job)] = job
                    next_job_index += 1

                if stop_submitting and next_job_index < len(parallel_send_jobs):
                    remaining_jobs = parallel_send_jobs[next_job_index:]
                    for job in remaining_jobs:
                        entry = job["entry"]
                        entry["result"] = "stopped_before_send"
                        entry["next_action"] = "Отправка этой и следующих строк остановлена по запросу пользователя."
                        state["ready_rows"] -= 1
                        processed_entries.append(entry)
                        state["processed_rows"] += 1
                    state["rows"] = _state_rows_snapshot(processed_entries)
                    _save_sender_state(state, job_id)
                    next_job_index = len(parallel_send_jobs)

    if not dry_run:
        if workbook_dirty:
            final_flush_warning = _flush_sender_workbook(workbook, data_xlsx_path)
            if final_flush_warning:
                runtime_warnings.append(final_flush_warning)
        state["stats"] = _collect_excel_stats(data_xlsx_path)
        state["remaining_rows"] = int(state["stats"].get("pending", 0)) + int(state["stats"].get("error", 0))
    else:
        state["remaining_rows"] = max(0, len(rows) - len(candidates))

    state["rows"] = _state_rows_snapshot(processed_entries)
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
    state["status"] = "stopped" if state.get("stop_requested") else "completed"
    state["task_stats"] = count_tasks_for_agent("sender", job_id)
    state["tasks"] = get_tasks_for_agent("sender", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="sender", limit=20, job_id=job_id)
    state["summary_text"] = _format_sender_summary(state)
    if runtime_warnings:
        unique_warnings = list(dict.fromkeys(item for item in runtime_warnings if item))
        if unique_warnings:
            state["summary_text"] = f"{state['summary_text']} {' '.join(unique_warnings)}".strip()
    _save_sender_state(state, job_id)
    return dict(state)


def get_sender_status(job_id: str | None = None) -> dict[str, Any]:
    state = _load_sender_state(job_id)
    data_xlsx_path = _resolve_sender_data_xlsx_path(job_id)
    state["stats"] = _collect_excel_stats(data_xlsx_path)
    state["task_stats"] = count_tasks_for_agent("sender", job_id)
    state["tasks"] = get_tasks_for_agent("sender", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="sender", limit=20, job_id=job_id)
    return state


def _fallback_sender_chat(message: str, state: dict[str, Any], *, job_id: str | None = None) -> str:
    rows = state.get("rows") or []
    preview = preview_recipients(limit=10, job_id=job_id)
    tasks = state.get("tasks") or []
    recent_events = state.get("recent_events") or []
    if not rows:
        data_xlsx_path = _resolve_sender_data_xlsx_path(job_id)
        stats = state.get("stats") or _collect_excel_stats(data_xlsx_path)
        extra = ""
        if tasks:
            extra = (
                "\nВнутренние задачи между агентами:\n"
                + "\n".join(
                    f"- {item.get('source_agent')} -> sender: {item.get('task_type')} "
                    f"для строки {item.get('row_id')} ({item.get('status')})"
                    for item in tasks[:5]
                )
            )
        if recent_events:
            extra += (
                "\nПоследние внутренние сообщения:\n"
                + "\n".join(
                    f"- {item.get('source_agent')} -> {item.get('target_agent') or 'system'}: {item.get('message')}"
                    for item in recent_events[-3:]
                )
            )
        return (
            f"Рассылка ещё не запускалась. Сейчас в Excel: всего {stats['total']}, "
            f"отправлено {stats['sent']}, ошибок {stats['error']}, ожидают {stats['pending']}.\n"
            f"{preview.get('summary_text')}\n"
            f"{_format_preview_rows(preview.get('rows') or [], limit=5)}"
            f"{extra}"
        )
    return (
        (state.get("summary_text") or "Статус отправщика пока недоступен.")
        + "\nПоследние обработанные строки:\n"
        + _format_preview_rows(
            [
                {
                    "id": item.get("id"),
                    "mun_name": item.get("mun_name"),
                    "recipient": item.get("recipient"),
                    "decision_reason": item.get("decision_reason"),
                    "email_strategy": item.get("email_strategy"),
                }
                for item in rows[:5]
            ],
            limit=5,
        )
        + (
            "\nПоследние внутренние сообщения:\n"
            + "\n".join(
                f"- {item.get('source_agent')} -> {item.get('target_agent') or 'system'}: {item.get('message')}"
                for item in recent_events[-3:]
            )
            if recent_events else ""
        )
    )


def _build_openai_client() -> OpenAI | None:
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    base_url = _resolve_openai_base_url()
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    try:
        return OpenAI(**kwargs)
    except Exception:
        return None


def chat_with_sender(message: str, *, job_id: str | None = None) -> dict[str, Any]:
    state = get_sender_status(job_id)
    client = _build_openai_client()
    preview = preview_recipients(limit=30, job_id=job_id)
    if not client:
        return {"reply": _fallback_sender_chat(message, state, job_id=job_id), "state": state}

    compact_rows = []
    for item in (state.get("rows") or [])[:20]:
        compact_rows.append(
            {
                "id": item.get("id"),
                "mun_name": item.get("mun_name"),
                "result": item.get("result"),
                "recipient": item.get("recipient"),
                "error": item.get("error"),
            }
        )

    prompt = (
        "Ты агент-отправщик писем с КП и договором. "
        "Отвечай кратко, по-русски, только на основе текущего состояния запуска и предпросмотра адресов из data.xlsx. "
        "Если пользователь спрашивает про адреса или почты до рассылки, опирайся на предпросмотр, а не проси запускать отправку. "
        "Не выдумывай информацию, которой нет в данных.\n\n"
        f"Состояние последнего запуска:\n{json.dumps({'summary_text': state.get('summary_text'), 'stats': state.get('stats'), 'rows': compact_rows, 'task_stats': state.get('task_stats'), 'tasks': (state.get('tasks') or [])[:10], 'recent_events': (state.get('recent_events') or [])[:10]}, ensure_ascii=False, indent=2)}\n\n"
        f"Предпросмотр адресов из data.xlsx:\n{json.dumps(preview, ensure_ascii=False, indent=2)}\n\n"
        f"Вопрос пользователя:\n{message}"
    )

    request_kwargs = {
        "model": settings.case_agent_model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if not _resolve_openai_base_url():
        request_kwargs["response_format"] = {"type": "text"}

    try:
        response = client.chat.completions.create(**request_kwargs)
        reply = _safe_text(response.choices[0].message.content)
        if not reply:
            reply = _fallback_sender_chat(message, state, job_id=job_id)
    except Exception:
        reply = _fallback_sender_chat(message, state, job_id=job_id)

    return {"reply": reply, "state": state}
