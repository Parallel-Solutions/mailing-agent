from __future__ import annotations

import imaplib
import smtplib
import json
import re
import base64
import mimetypes
import secrets
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.generator.ai_case_agent import (
    OpenAI,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.generator.agent_handoff import (
    count_tasks_for_agent,
    create_task,
    get_recent_events,
    get_tasks_for_agent,
)
from src.generator.config_generator import DATA_DIR, DATA_XLSX_PATH, OUTPUT_DIR, TEMPLATES_DIR
from src.generator.excel_io import load_rows, save_workbook, update_status
from src.generator.generator_agent import run_generator_agent
from src.generator.philologist_agent import run_philologist
from src.generator.responsibility_matrix import diagnose_responsibility
from src.jobs import load_agent_state, resolve_job_paths, save_agent_state
from src.utils.config import settings


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAIL_TEMPLATE_PATH = TEMPLATES_DIR / "mail_template.txt"
SENT_MAIL_LOG_PATH = DATA_DIR / "sent_mail_log.jsonl"
DEFAULT_MAIL_SUBJECT = "Коммерческое предложение МНГП. Срок действия до 31.05.2026"
DEFAULT_MAIL_BODY = (
    "Уважаемый(ая) {HEAD_FIO}!\n\n"
    "Направляем в адрес {ADM_NAME} коммерческое предложение и проект договора.\n"
    "Просим при ответе указать входящий номер письма.\n\n"
    "С уважением,\n"
    "ООО «ПР»"
)
DEFAULT_MAIL_FOOTER_TEXT = (
    "С уважением,\n"
    "Черкашина Наталья Александровна\n"
    "+7 (812) 242-93-12\n"
    "ООО «Параллельные Решения»\n"
    "https://www.parresh.ru/"
)
MAIL_FOOTER_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "parresh-signature-logo.png"
MAIL_FOOTER_LOGO_CID = "parresh-signature-logo"
MAIL_FOOTER_HTML_TEMPLATE = """
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:16px;border-collapse:collapse">
  <tr><td style="padding:0"><img src="{image_src}" alt="Параллельные Решения" width="340" style="display:block;width:340px;max-width:340px;height:auto;border:0;outline:none;text-decoration:none"></td></tr>
  <tr><td style="padding:6px 0 0 0;font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;font-size:13px;line-height:1.3"><a href="https://www.parresh.ru/" style="color:#1f5da8;text-decoration:underline">https://www.parresh.ru/</a></td></tr>
</table>
""".strip()
STATUS_OK_VALUES = {"ОК", "OK", "SENT"}
UNISENDER_GO_SEND_PATH = "email/send.json"
UNISENDER_CLASSIC_SEND_PATH = "sendEmail"


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


def _load_sender_state(job_id: str | None = None) -> dict[str, Any]:
    return load_agent_state("sender", SENDER_STATE, job_id)


def _save_sender_state(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    return save_agent_state("sender", state, job_id)


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


def _read_mail_template(mail_template_path: Path | None = None) -> str:
    template_path = mail_template_path or MAIL_TEMPLATE_PATH
    if template_path.exists():
        try:
            text = template_path.read_text(encoding="utf-8-sig").strip()
            if text:
                return text
        except OSError:
            pass
    return DEFAULT_MAIL_BODY


def _build_mail_footer_html(*, inline_image: bool = False) -> str:
    return MAIL_FOOTER_HTML_TEMPLATE.format(image_src=_mail_footer_image_src(inline=inline_image))


def _mail_footer_image_src(*, inline: bool = False) -> str:
    if inline:
        return f"cid:{MAIL_FOOTER_LOGO_CID}"
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
    recipient = email_decision.get("recipient")
    if not recipient:
        return []
    # Safety rule: send only to the chosen recipient.
    # EMAIL_DOP may help choose a recipient when EMAIL_OSN is empty/invalid,
    # but it must not be used as an automatic fallback destination after SMTP failure.
    return [recipient]


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
            summary += f" Осталось строк без статуса ОК: {state.get('remaining_rows', 0)}."
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
        summary += f" После этой партии осталось строк без статуса ОК: {state.get('remaining_rows', 0)}."
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

    for row in candidates:
        email_decision = _choose_recipient(row)
        status_class = _status_class(row.get("STATUS"))
        entry = {
            "id": row.get("ID"),
            "mun_name": _safe_text(row.get("MUN_NAME")),
            "status_before": _safe_text(row.get("STATUS")),
            "status_class": status_class,
            "recipient": email_decision["recipient"],
            "email_strategy": email_decision["strategy"],
            "decision_reason": email_decision["decision_reason"],
            "primary_emails": _parse_emails(row.get("EMAIL_OSN")),
            "extra_emails": _parse_emails(row.get("EMAIL_DOP")),
            "invalid_emails": email_decision["invalid_emails"],
            "fallback_candidates": email_decision["fallback_candidates"],
        }
        if not entry["recipient"]:
            missing_count += 1
        if entry["email_strategy"] == "fallback_extra":
            fallback_count += 1
        if entry["invalid_emails"]:
            invalid_count += 1
        preview_rows.append(entry)

    summary_text = (
        f"Предпросмотр адресов: строк {len(preview_rows)}, "
        f"без адреса {missing_count}, с резервным адресом {fallback_count}, "
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
    body = _append_mail_footer_text(_read_mail_template(mail_template_path).format(
        HEAD_FIO=_safe_text(row.get("HEAD_FIO")),
        ADM_NAME=_safe_text(row.get("ADM_NAME")),
        MUN_NAME=_safe_text(row.get("MUN_NAME")),
    ))
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
    return _append_mail_footer_text(_read_mail_template(mail_template_path).format(
        HEAD_FIO=_safe_text(row.get("HEAD_FIO")),
        ADM_NAME=_safe_text(row.get("ADM_NAME")),
        MUN_NAME=_safe_text(row.get("MUN_NAME")),
    ))


def _append_sent_mail_log(
    *,
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    transport: str,
    warning: str = "",
    sent_mail_log_path: Path | None = None,
) -> str | None:
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


def _send_via_unisender_classic(
    row: dict[str, Any],
    recipient: str,
    attachments: list[str],
    subject: str,
    *,
    mail_template_path: Path | None = None,
) -> str | None:
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

    from urllib.parse import urlencode

    request = Request(
        _build_unisender_classic_url(UNISENDER_CLASSIC_SEND_PATH),
        data=urlencode(payload).encode("utf-8", errors="ignore"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8", errors="replace")
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
            return None

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
) -> str | None:
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
            "idempotence_key": secrets.token_urlsafe(24),
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
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
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
    return None


def _send_with_transport(
    row: dict[str, Any],
    recipients: list[str],
    attachments: list[str],
    subject: str,
    *,
    transport: str,
    mail_template_path: Path | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, str]] = []
    for recipient in recipients:
        try:
            warning = None
            if transport == "unisender":
                warning = _send_via_unisender(
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
        attempts.append({"recipient": recipient, "status": "sent", "error": ""})
        return {"recipient": recipient, "attempts": attempts, "error": "", "warning": warning or ""}
    last_error = attempts[-1]["error"] if attempts else "Не найден получатель для отправки."
    return {"recipient": None, "attempts": attempts, "error": last_error, "warning": ""}


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
    job_template_path = job_paths.templates_dir / "mail_template.txt"
    mail_template_path = None if job_paths.uses_legacy_layout or not job_template_path.exists() else job_template_path
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
    started_at = perf_counter()
    subject = DEFAULT_MAIL_SUBJECT

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
            state["rows"] = processed_entries
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
            state["error_rows"] += 1
            processed_entries.append(entry)
            state["processed_rows"] += 1
            state["rows"] = processed_entries
            _save_sender_state(state, job_id)
            continue

        state["ready_rows"] += 1

        if not dry_run:
            _refresh_sender_stop_flag(state, job_id)
            if state.get("stop_requested"):
                entry["result"] = "stopped_before_send"
                entry["next_action"] = "Отправка этой и следующих строк остановлена по запросу пользователя."
                state["ready_rows"] -= 1
                processed_entries.append(entry)
                state["processed_rows"] += 1
                state["rows"] = processed_entries
                _save_sender_state(state, job_id)
                break
            try:
                send_result = _send_with_transport(
                    row,
                    _allowed_send_recipients(email_decision),
                    attachments,
                    subject,
                    transport=effective_transport,
                    mail_template_path=mail_template_path,
                )
                entry["attempts"] = send_result["attempts"]
                entry["warning"] = _safe_text(send_result.get("warning"))
                if not send_result["recipient"]:
                    raise RuntimeError(send_result["error"])
                entry["recipient"] = send_result["recipient"]
                if entry["email_strategy"] == "fallback_extra" or entry["recipient"] != email_decision["recipient"]:
                    entry["decision_reason"] = "Письмо отправлено по резервному email после выбора лучшего доступного адреса."
                log_warning = _append_sent_mail_log(
                    row=row,
                    recipient=entry["recipient"],
                    attachments=attachments,
                    subject=subject,
                    transport=effective_transport,
                    warning=entry["warning"],
                    sent_mail_log_path=sent_mail_log_path,
                )
                if log_warning:
                    entry["warning"] = (
                        f"{entry['warning']} {log_warning}".strip() if entry["warning"] else log_warning
                    )
                if entry["warning"]:
                    state["warning_rows"] += 1
                    entry["next_action"] = entry["warning"]
                update_status(worksheet, row["_row_index"], "ОК")
            except Exception as exc:
                entry["result"] = "error_send"
                entry["error"] = _safe_text(exc) or "Ошибка SMTP-отправки."
                entry["next_action"] = "Повторить отправку позже или передать строку на ручную проверку."
                state["ready_rows"] -= 1
                state["error_rows"] += 1
            else:
                state["sent_rows"] += 1
                delay_seconds = 0.0 if effective_transport == "unisender" else max(0.0, float(settings.sender_delay_seconds or 0))
                if delay_seconds > 0 and state["processed_rows"] + 1 < state["total_rows"]:
                    sleep(delay_seconds)
        else:
            entry["attempts"] = [
                {
                    "recipient": entry["recipient"],
                    "status": "ready",
                    "error": "",
                }
            ]

        processed_entries.append(entry)
        state["processed_rows"] += 1
        state["rows"] = processed_entries
        _save_sender_state(state, job_id)

    if not dry_run:
        save_workbook(workbook, data_xlsx_path)
        state["stats"] = _collect_excel_stats(data_xlsx_path)
        state["remaining_rows"] = int(state["stats"].get("pending", 0))
    else:
        state["remaining_rows"] = max(0, len(rows) - len(candidates))

    state["rows"] = processed_entries
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
    state["status"] = "stopped" if state.get("stop_requested") else "completed"
    state["task_stats"] = count_tasks_for_agent("sender", job_id)
    state["tasks"] = get_tasks_for_agent("sender", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="sender", limit=20, job_id=job_id)
    state["summary_text"] = _format_sender_summary(state)
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
