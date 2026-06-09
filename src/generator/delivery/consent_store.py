from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document

from src.jobs import resolve_job_paths
from src.utils.config import settings


CONSENT_FILENAME = "consents.json"
CONSENT_TEXT = "Согласен получить коммерческое предложение и проект договора от ООО «Параллельные Решения»."
CONSENT_OPERATOR_NAME = "ООО «Параллельные Решения»"
CONSENT_OPERATOR_INN = "5038110107"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _consent_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / CONSENT_FILENAME


def _consent_documents_dir(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).consents_dir


def _safe_filename_part(value: Any, fallback: str = "item") -> str:
    text = _safe_text(value).lower()
    text = re.sub(r"[^a-zа-яё0-9_-]+", "-", text, flags=re.IGNORECASE)
    text = text.strip("-_")
    return (text or fallback)[:80]


def _recipient_hash(value: Any) -> str:
    return hashlib.sha256(_safe_text(value).lower().encode("utf-8")).hexdigest()[:12]


def _relative_to_job_root(path: Path, *, job_id: str | None) -> str:
    root_dir = resolve_job_paths(job_id).root_dir
    try:
        return path.relative_to(root_dir).as_posix()
    except ValueError:
        return str(path)


def _consent_document_path(record: dict[str, Any], *, job_id: str | None) -> Path:
    confirmed_at = _safe_text(record.get("confirmed_at")) or datetime.now().isoformat(timespec="seconds")
    date_part = confirmed_at[:10] if len(confirmed_at) >= 10 else datetime.now().strftime("%Y-%m-%d")
    row_part = _safe_filename_part(record.get("row_id"), fallback="row")
    recipient_part = _recipient_hash(record.get("recipient"))
    timestamp_part = re.sub(r"[^0-9]+", "", confirmed_at)[:14] or datetime.now().strftime("%Y%m%d%H%M%S")
    return (
        _consent_documents_dir(job_id)
        / date_part
        / f"row-{row_part}_{recipient_part}_{timestamp_part}_consent.docx"
    )


def _add_consent_paragraph(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.add_run(text)


def _save_consent_document(record: dict[str, Any], *, job_id: str | None) -> Path:
    existing_path = _safe_text(record.get("consent_document_path"))
    if existing_path:
        candidate = resolve_job_paths(job_id).root_dir / existing_path
        if candidate.exists():
            return candidate

    path = _consent_document_path(record, job_id=job_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    document.add_heading("СОГЛАСИЕ", level=1)
    document.add_heading("НА ПОЛУЧЕНИЕ МАТЕРИАЛОВ И ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ", level=2)

    _add_consent_paragraph(
        document,
        (
            f"Настоящим я, перейдя по ссылке из письма, направленного {CONSENT_OPERATOR_NAME}, "
            f"ИНН {CONSENT_OPERATOR_INN} (далее — Оператор), как субъект персональных данных, "
            "во исполнение требований Федерального закона от 27.07.2006 № 152-ФЗ "
            "«О персональных данных», добровольно, своей волей и в своем интересе предоставляю "
            "своё согласие на:"
        ),
    )
    _add_consent_paragraph(
        document,
        "1. Получение от Оператора по электронной почте коммерческого предложения, проекта договора, "
        "технического задания, календарного плана и иных материалов.",
    )
    _add_consent_paragraph(
        document,
        "2. Обработку моих персональных данных: адрес электронной почты, IP-адрес, время и факт "
        "перехода по ссылке, данные об устройстве.",
    )
    _add_consent_paragraph(
        document,
        "Цель обработки: направление запрошенных мной материалов, а также последующее информирование "
        "о товарах, работах, услугах Оператора, включая рекламные и информационные рассылки, "
        "в рамках уставной деятельности Оператора.",
    )
    _add_consent_paragraph(
        document,
        "Перечень действий с персональными данными: сбор, запись, систематизация, накопление, "
        "хранение, уточнение (обновление, изменение), извлечение, использование, передача "
        "(распространение, предоставление, доступ) в объёме, необходимом для достижения указанной цели, "
        "а также блокирование, удаление, уничтожение.",
    )
    _add_consent_paragraph(
        document,
        "Согласие действует бессрочно либо до момента моего отзыва через ссылку отписки в каждом письме "
        "или по запросу на email: personal.offer@parresh.ru.",
    )
    _add_consent_paragraph(
        document,
        "Я подтверждаю, что переход по ссылке является аналогом собственноручной подписи и полностью "
        "заменяет её для целей фиксации согласия в информационной системе Оператора.",
    )

    document.add_heading("Фиксация согласия произведена:", level=2)
    _add_consent_paragraph(document, f"Дата и время: {_safe_text(record.get('confirmed_at'))}")
    _add_consent_paragraph(document, f"Уникальный ID получателя: {_safe_text(record.get('token'))}")
    _add_consent_paragraph(document, f"Email получателя: {_safe_text(record.get('recipient'))}")
    _add_consent_paragraph(document, f"Муниципальное образование: {_safe_text(record.get('mun_name'))}")
    _add_consent_paragraph(document, f"ID строки: {_safe_text(record.get('row_id'))}")
    _add_consent_paragraph(document, f"IP-адрес: {_safe_text(record.get('confirmed_ip'))}")
    _add_consent_paragraph(document, f"User-Agent: {_safe_text(record.get('confirmed_user_agent'))}")

    document.save(path)
    return path


def _load_records(job_id: str | None) -> list[dict[str, Any]]:
    path = _consent_path(job_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    records = data.get("records") if isinstance(data, dict) else None
    return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []


def _save_records(job_id: str | None, records: list[dict[str, Any]]) -> None:
    path = _consent_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_matches(record: dict[str, Any], *, row_id: Any, recipient: str) -> bool:
    return (
        _safe_text(record.get("row_id")) == _safe_text(row_id)
        and _safe_text(record.get("recipient")).lower() == _safe_text(recipient).lower()
    )


def public_consent_url(token: str) -> str:
    base_url = _safe_text(settings.public_base_url).rstrip("/")
    if not base_url:
        base_url = "http://127.0.0.1:8000"
    return f"{base_url}/consent/confirm/{token}"


def prepare_consent_request(
    *,
    job_id: str | None,
    row: dict[str, Any],
    recipient: str,
    transport: str,
) -> dict[str, Any]:
    records = _load_records(job_id)
    row_id = row.get("ID")
    now = datetime.now().isoformat(timespec="seconds")
    for record in records:
        if _record_matches(record, row_id=row_id, recipient=recipient):
            record.setdefault("token", secrets.token_urlsafe(24))
            record["status"] = _safe_text(record.get("status")) or "pending"
            record["last_request_prepared_at"] = now
            record["transport"] = _safe_text(transport)
            _save_records(job_id, records)
            return dict(record, consent_url=public_consent_url(record["token"]))

    token = secrets.token_urlsafe(24)
    record = {
        "token": token,
        "status": "pending",
        "job_id": _safe_text(job_id),
        "row_id": _safe_text(row_id),
        "mun_name": _safe_text(row.get("MUN_NAME")),
        "recipient": _safe_text(recipient),
        "consent_text": CONSENT_TEXT,
        "created_at": now,
        "last_request_prepared_at": now,
        "transport": _safe_text(transport),
    }
    records.append(record)
    _save_records(job_id, records)
    return dict(record, consent_url=public_consent_url(token))


def mark_consent_request_sent(
    *,
    job_id: str | None,
    row_id: Any,
    recipient: str,
    provider: dict[str, Any] | None = None,
) -> None:
    records = _load_records(job_id)
    now = datetime.now().isoformat(timespec="seconds")
    for record in records:
        if _record_matches(record, row_id=row_id, recipient=recipient):
            record["status"] = "request_sent"
            record["request_sent_at"] = now
            if provider:
                record["provider"] = provider
            _save_records(job_id, records)
            return


def has_confirmed_consent(*, job_id: str | None, row_id: Any, recipient: str) -> bool:
    for record in _load_records(job_id):
        if _record_matches(record, row_id=row_id, recipient=recipient):
            return _safe_text(record.get("status")) == "confirmed"
    return False


def get_consent_by_token(token: str) -> dict[str, Any] | None:
    clean_token = _safe_text(token)
    for job_dir in _iter_job_dirs():
        job_id = job_dir.name if job_dir.name.startswith("job-") else None
        for record in _load_records(job_id):
            if _safe_text(record.get("token")) == clean_token:
                return dict(record)
    return None


def confirm_consent(token: str, *, ip: str = "", user_agent: str = "") -> dict[str, Any] | None:
    clean_token = _safe_text(token)
    for job_dir in _iter_job_dirs():
        job_id = job_dir.name if job_dir.name.startswith("job-") else None
        records = _load_records(job_id)
        changed = False
        for record in records:
            if _safe_text(record.get("token")) != clean_token:
                continue
            already_confirmed = _safe_text(record.get("status")) == "confirmed"
            record["status"] = "confirmed"
            if not already_confirmed or not _safe_text(record.get("confirmed_at")):
                record["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
            if not already_confirmed or not _safe_text(record.get("confirmed_ip")):
                record["confirmed_ip"] = _safe_text(ip)
            if not already_confirmed or not _safe_text(record.get("confirmed_user_agent")):
                record["confirmed_user_agent"] = _safe_text(user_agent)
            record["materials_dispatch_requested_at"] = record["confirmed_at"]
            document_path = _save_consent_document(record, job_id=job_id)
            record["consent_document_path"] = _relative_to_job_root(document_path, job_id=job_id)
            changed = True
            if changed:
                _save_records(job_id, records)
            return dict(record)
    return None


def _iter_job_dirs() -> list[Path]:
    jobs_root = resolve_job_paths("job-placeholder").root_dir.parent
    candidates: list[Path] = []
    if jobs_root.exists():
        candidates.extend(path for path in jobs_root.iterdir() if path.is_dir())
    legacy_root = resolve_job_paths(None).root_dir
    if legacy_root.exists():
        candidates.append(legacy_root)
    return candidates
