from __future__ import annotations

import imaplib
import smtplib
import json
import re
import base64
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlencode
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
from src.generator.config_generator import DATA_XLSX_PATH, OUTPUT_DIR, TEMPLATES_DIR
from src.generator.excel_io import load_rows, save_workbook, update_status
from src.generator.generator_agent import run_generator_agent
from src.generator.philologist_agent import run_philologist
from src.generator.responsibility_matrix import diagnose_responsibility
from src.utils.config import settings


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAIL_TEMPLATE_PATH = TEMPLATES_DIR / "mail_template.txt"
DEFAULT_MAIL_SUBJECT = "Коммерческое предложение МНГП. Срок действия до 31.05.2026"
DEFAULT_MAIL_BODY = (
    "Уважаемый(ая) {HEAD_FIO}!\n\n"
    "Направляем в адрес {ADM_NAME} коммерческое предложение и проект договора.\n"
    "Просим при ответе указать входящий номер письма.\n\n"
    "С уважением,\n"
    "ООО «ПР»"
)
STATUS_OK_VALUES = {"ОК", "OK", "SENT"}
UNISENDER_SEND_URL = "https://api.unisender.com/ru/api/sendEmail"


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


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_mail_template() -> str:
    if MAIL_TEMPLATE_PATH.exists():
        try:
            text = MAIL_TEMPLATE_PATH.read_text(encoding="utf-8-sig").strip()
            if text:
                return text
        except OSError:
            pass
    return DEFAULT_MAIL_BODY


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


def _resolve_output_folder(row_id: Any) -> tuple[Path | None, str | None]:
    if row_id in (None, ""):
        return None, "Не указан ID строки."

    prefix = f"{row_id}_"
    matches = [path for path in OUTPUT_DIR.iterdir() if path.is_dir() and path.name.startswith(prefix)] if OUTPUT_DIR.exists() else []
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


def _collect_excel_stats() -> dict[str, int]:
    if not DATA_XLSX_PATH.exists():
        return {"total": 0, "sent": 0, "error": 0, "pending": 0}

    _, _, rows = load_rows(DATA_XLSX_PATH)
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
        if dry_run:
            return None
        return max(1, int(settings.sender_default_batch_size))

    normalized = max(1, int(limit))
    return min(normalized, max(1, int(settings.sender_max_batch_size)))


def _normalize_transport(transport: str | None) -> str:
    value = _safe_text(transport).lower()
    if value in {"unisender", "smtp"}:
        return value
    configured = _safe_text(settings.sender_transport).lower()
    return configured if configured in {"unisender", "smtp"} else "smtp"


def request_sender_stop() -> dict[str, Any]:
    SENDER_STATE["stop_requested"] = True
    SENDER_STATE["stop_requested_at"] = datetime.now().isoformat(timespec="seconds")
    if SENDER_STATE.get("status") == "running":
        SENDER_STATE["summary_text"] = (
            "Получен запрос на остановку. Отправщик завершит текущую строку и больше не будет брать новые."
        )
    return get_sender_status()


def clear_sender_stop_request() -> None:
    SENDER_STATE["stop_requested"] = False
    SENDER_STATE["stop_requested_at"] = None


def _active_sender_review_task(row_id: Any) -> dict[str, Any] | None:
    row_id_text = _safe_text(row_id)
    for item in get_tasks_for_agent("sender"):
        if _safe_text(item.get("task_type")) != "review_before_send":
            continue
        if _safe_text(item.get("row_id")) != row_id_text:
            continue
        if _safe_text(item.get("status")) not in {"pending", "in_progress"}:
            continue
        return item
    return None


def _retry_row_resources(row_id: Any) -> tuple[Path | None, str | None, list[str], str | None]:
    folder, folder_error = _resolve_output_folder(row_id)
    attachments, attachment_error = _resolve_pdf_attachments(folder)
    return folder, folder_error, attachments, attachment_error


def _delegate_sender_problem(
    *,
    symptom: str,
    row_id: Any,
    mun_name: str,
    details: dict[str, Any],
) -> dict[str, Any]:
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
    )


def _run_autonomous_recovery_for_generator(*, row_id: Any) -> dict[str, Any]:
    row_id_text = _safe_text(row_id)
    generator_result = run_generator_agent(row_ids=[row_id_text] if row_id_text else None)
    result = {
        "generator_result": generator_result,
    }
    if settings.philologist_auto_run_enabled:
        result["philologist_result"] = run_philologist(
            ai_enabled=True,
            row_ids=[row_id_text] if row_id_text else None,
        )
    return result


def _run_autonomous_recovery_for_philologist(*, row_id: Any) -> dict[str, Any]:
    if not settings.philologist_auto_run_enabled:
        return {"philologist_result": {"status": "skipped", "summary_text": "Автозапуск филолога отключён."}}
    row_id_text = _safe_text(row_id)
    philologist_result = run_philologist(ai_enabled=True, row_ids=[row_id_text] if row_id_text else None)
    return {"philologist_result": philologist_result}


def preview_recipients(*, limit: int | None = None) -> dict[str, Any]:
    if not DATA_XLSX_PATH.exists():
        return {
            "status": "error",
            "summary_text": "Файл data.xlsx не найден.",
            "rows": [],
            "total_rows": 0,
        }

    _, _, rows = load_rows(DATA_XLSX_PATH)
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


def _build_message(row: dict[str, Any], recipient: str, attachments: list[str], subject: str) -> EmailMessage:
    body = _read_mail_template().format(
        HEAD_FIO=_safe_text(row.get("HEAD_FIO")),
        ADM_NAME=_safe_text(row.get("ADM_NAME")),
        MUN_NAME=_safe_text(row.get("MUN_NAME")),
    )
    message = EmailMessage()
    message["From"] = settings.smtp_sender_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    for attachment_path in attachments:
        path = Path(attachment_path)
        message.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=path.name,
        )
    return message


def _build_mail_body(row: dict[str, Any]) -> str:
    return _read_mail_template().format(
        HEAD_FIO=_safe_text(row.get("HEAD_FIO")),
        ADM_NAME=_safe_text(row.get("ADM_NAME")),
        MUN_NAME=_safe_text(row.get("MUN_NAME")),
    )


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


def _send_via_smtp(row: dict[str, Any], recipient: str, attachments: list[str], subject: str) -> str | None:
    if not settings.smtp_allow_real_send:
        raise RuntimeError(
            "Реальная SMTP-отправка запрещена настройкой smtp_allow_real_send. "
            "Сейчас доступен только dry-run режим."
        )
    if not settings.smtp_sender_email or not settings.smtp_sender_password:
        raise RuntimeError("Не настроены SMTP-учётные данные отправителя.")
    if not settings.smtp_host:
        raise RuntimeError("Не указан SMTP host.")

    message = _build_message(row, recipient, attachments, subject)

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


def _htmlify_mail_body(body: str) -> str:
    parts = [line.strip() for line in body.splitlines()]
    non_empty = [line for line in parts if line]
    html = "<br>".join(non_empty)
    if "{{UnsubscribeUrl}}" not in html:
        html += "<br><br><a href='{{UnsubscribeUrl}}'>Отписаться от писем</a>"
    return html


def _send_via_unisender(row: dict[str, Any], recipient: str, attachments: list[str], subject: str) -> str | None:
    api_key = _safe_text(settings.unisender_api_key)
    sender_email = _safe_text(settings.unisender_sender_email or settings.smtp_sender_email)
    sender_name = _safe_text(settings.unisender_sender_name) or "ООО «ПР»"
    list_id = int(settings.unisender_list_id or 1)
    if not api_key:
        raise RuntimeError("Не указан API-ключ UniSender.")
    if not sender_email:
        raise RuntimeError("Не указан подтверждённый email отправителя UniSender.")

    body = _htmlify_mail_body(_build_mail_body(row))
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
        UNISENDER_SEND_URL,
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


def _send_with_transport(
    row: dict[str, Any],
    recipients: list[str],
    attachments: list[str],
    subject: str,
    *,
    transport: str,
) -> dict[str, Any]:
    attempts: list[dict[str, str]] = []
    for recipient in recipients:
        try:
            warning = None
            if transport == "unisender":
                warning = _send_via_unisender(row, recipient, attachments, subject)
            else:
                warning = _send_via_smtp(row, recipient, attachments, subject)
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
    auto_recover: bool = True,
    row_ids: list[str] | None = None,
    transport: str | None = None,
) -> dict[str, Any]:
    state = SENDER_STATE
    clear_sender_stop_request()
    effective_limit = _normalize_limit(limit, dry_run=dry_run)
    effective_transport = _normalize_transport(transport)
    stats = _collect_excel_stats()
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
            "task_stats": count_tasks_for_agent("sender"),
            "tasks": get_tasks_for_agent("sender")[:20],
            "recent_events": get_recent_events(agent_name="sender", limit=20),
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

    if not DATA_XLSX_PATH.exists():
        state["status"] = "error"
        state["summary_text"] = "Файл data.xlsx не найден."
        return dict(state)

    workbook, worksheet, rows = load_rows(DATA_XLSX_PATH)
    requested_row_ids = {str(item).strip() for item in (row_ids or []) if str(item).strip()}
    if requested_row_ids:
        rows = [row for row in rows if str(row.get("ID")).strip() in requested_row_ids]
    candidates = rows[:effective_limit] if effective_limit else rows
    state["total_rows"] = len(candidates)

    processed_entries: list[dict[str, Any]] = []
    started_at = perf_counter()
    subject = DEFAULT_MAIL_SUBJECT

    for row in candidates:
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
            continue

        email_decision = _choose_recipient(row)
        entry["recipient"] = email_decision["recipient"]
        entry["emails"] = email_decision["valid_emails"]
        entry["invalid_emails"] = email_decision["invalid_emails"]
        entry["email_strategy"] = email_decision["strategy"]
        entry["decision_reason"] = email_decision["decision_reason"]
        entry["fallback_candidates"] = email_decision["fallback_candidates"]

        folder, folder_error = _resolve_output_folder(row_id)
        entry["folder"] = str(folder) if folder else None
        attachments, attachment_error = _resolve_pdf_attachments(folder)
        entry["attachments"] = attachments
        review_task = _active_sender_review_task(row_id)
        recovery_info: dict[str, Any] | None = None

        if not entry["recipient"]:
            entry["result"] = "needs_enrichment"
            entry["error"] = "Не найден валидный email получателя."
            entry["next_action"] = "Запросить у агента-парсера поиск или уточнение email."
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
            )
            entry["handoff_task_id"] = task.get("id")
            state["handoff_rows"] += 1
        elif folder_error:
            entry["result"] = "error_missing_output"
            entry["error"] = folder_error
            entry["next_action"] = "Передать генератору задачу на пересборку комплекта документов."
            task = _delegate_sender_problem(
                symptom="missing_output",
                row_id=row_id,
                mun_name=entry["mun_name"],
                details={
                    "reason": folder_error,
                    "folder_exists": False,
                    "attachment_count": 0,
                },
            )
            entry["handoff_task_id"] = task.get("id")
            state["generator_handoff_rows"] += 1
            if auto_recover:
                recovery_info = _run_autonomous_recovery_for_generator(row_id=row_id)
                folder, folder_error, attachments, attachment_error = _retry_row_resources(row_id)
                entry["folder"] = str(folder) if folder else None
                entry["attachments"] = attachments
                if not folder_error and not attachment_error and entry["recipient"]:
                    entry["result"] = "ready_after_recovery" if dry_run else "sent"
                    entry["decision_reason"] += " Генератор автоматически пересобрал комплект документов."
                    state["autonomous_recovery_rows"] += 1
        elif attachment_error:
            entry["result"] = "error_missing_attachments"
            entry["error"] = attachment_error
            entry["next_action"] = "Передать генератору задачу на восстановление вложений."
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
            )
            entry["handoff_task_id"] = task.get("id")
            state["generator_handoff_rows"] += 1
            if auto_recover:
                recovery_info = _run_autonomous_recovery_for_generator(row_id=row_id)
                folder, folder_error, attachments, attachment_error = _retry_row_resources(row_id)
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
                recovery_info = _run_autonomous_recovery_for_philologist(row_id=row_id)
                review_task = _active_sender_review_task(row_id)
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
            continue

        state["ready_rows"] += 1

        if not dry_run:
            if state.get("stop_requested"):
                entry["result"] = "stopped_before_send"
                entry["next_action"] = "Отправка этой и следующих строк остановлена по запросу пользователя."
                state["ready_rows"] -= 1
                processed_entries.append(entry)
                state["processed_rows"] += 1
                break
            try:
                send_result = _send_with_transport(
                    row,
                    _allowed_send_recipients(email_decision),
                    attachments,
                    subject,
                    transport=effective_transport,
                )
                entry["attempts"] = send_result["attempts"]
                entry["warning"] = _safe_text(send_result.get("warning"))
                if not send_result["recipient"]:
                    raise RuntimeError(send_result["error"])
                entry["recipient"] = send_result["recipient"]
                if entry["email_strategy"] == "fallback_extra" or entry["recipient"] != email_decision["recipient"]:
                    entry["decision_reason"] = "Письмо отправлено по резервному email после выбора лучшего доступного адреса."
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

    if not dry_run:
        save_workbook(workbook, DATA_XLSX_PATH)
        state["stats"] = _collect_excel_stats()
        state["remaining_rows"] = int(state["stats"].get("pending", 0))
    else:
        state["remaining_rows"] = max(0, len(rows) - len(candidates))

    state["rows"] = processed_entries
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
    state["status"] = "stopped" if state.get("stop_requested") else "completed"
    state["task_stats"] = count_tasks_for_agent("sender")
    state["tasks"] = get_tasks_for_agent("sender")[:20]
    state["recent_events"] = get_recent_events(agent_name="sender", limit=20)
    state["summary_text"] = _format_sender_summary(state)
    return dict(state)


def get_sender_status() -> dict[str, Any]:
    state = dict(SENDER_STATE)
    state["stats"] = _collect_excel_stats()
    state["task_stats"] = count_tasks_for_agent("sender")
    state["tasks"] = get_tasks_for_agent("sender")[:20]
    state["recent_events"] = get_recent_events(agent_name="sender", limit=20)
    return state


def _fallback_sender_chat(message: str, state: dict[str, Any]) -> str:
    rows = state.get("rows") or []
    preview = preview_recipients(limit=10)
    tasks = state.get("tasks") or []
    recent_events = state.get("recent_events") or []
    if not rows:
        stats = state.get("stats") or _collect_excel_stats()
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


def chat_with_sender(message: str) -> dict[str, Any]:
    state = get_sender_status()
    client = _build_openai_client()
    preview = preview_recipients(limit=30)
    if not client:
        return {"reply": _fallback_sender_chat(message, state), "state": state}

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
            reply = _fallback_sender_chat(message, state)
    except Exception:
        reply = _fallback_sender_chat(message, state)

    return {"reply": reply, "state": state}
