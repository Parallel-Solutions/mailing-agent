"""Mail / KP / contract templates with versions."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from docx import Document
from pypdf import PdfReader
from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import MailTemplate, TemplateVersion
from src.infra.object_store import delete as delete_object
from src.infra.object_store import get_bytes, put_bytes

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
FILE_TEMPLATE_EXTENSIONS = {
    "kp": {".docx", ".pdf", ".html", ".htm"},
    "contract": {".docx"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _extract_variables(text: str) -> list[dict[str, Any]]:
    names = sorted(set(VARIABLE_RE.findall(text or "")))
    return [
        {
            "name": name,
            "source": "recipient" if name in {"company", "contact_name", "email", "region"} else "user_input",
            "label": name,
        }
        for name in names
    ]


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _file_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".html", ".htm"}:
        return _decode_text(data)
    if suffix == ".docx":
        document = Document(BytesIO(data))
        chunks = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                chunks.extend(cell.text for cell in row.cells)
        for section in document.sections:
            chunks.extend(paragraph.text for paragraph in section.header.paragraphs)
            chunks.extend(paragraph.text for paragraph in section.footer.paragraphs)
        return "\n".join(chunks)
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return ""


def _validate_file_template_type(template_type: str, filename: str) -> str:
    normalized_type = str(template_type or "").strip().lower()
    allowed = FILE_TEMPLATE_EXTENSIONS.get(normalized_type)
    if not allowed:
        raise ValueError("Файловая загрузка доступна только для КП и договоров")
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        formats = ", ".join(sorted(allowed))
        raise ValueError(f"Для этого типа шаблона доступны форматы: {formats}")
    return normalized_type

def template_to_dict(row: MailTemplate, version: TemplateVersion | None = None) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "name": row.name,
        "template_type": row.template_type,
        "status": row.status,
        "active_version_id": row.active_version_id,
        "tags": list(row.tags or []),
        "archived": bool(row.archived),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if version is not None:
        payload["version"] = version_to_dict(version)
    return payload


def version_to_dict(row: TemplateVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "template_id": row.template_id,
        "version_number": row.version_number,
        "subject": row.subject,
        "body_html": row.body_html,
        "body_text": row.body_text,
        "variables": list(row.variables or []),
        "storage_key": row.storage_key,
        "filename": row.filename,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_template(
    owner_username: str,
    *,
    name: str,
    template_type: str,
    subject: str = "",
    body_html: str = "",
    body_text: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    template_id = _new_id()
    version_id = _new_id()
    combined = f"{subject}\n{body_html}\n{body_text}"
    with session_scope() as session:
        tmpl = MailTemplate(
            id=template_id,
            owner_username=owner_username,
            name=name or "Шаблон",
            template_type=template_type,
            status="ready",
            active_version_id=version_id,
            tags=list(tags or []),
        )
        session.add(tmpl)
        session.add(
            TemplateVersion(
                id=version_id,
                template_id=template_id,
                version_number=1,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                variables=_extract_variables(combined),
                created_by=owner_username,
            )
        )
        session.flush()
        return template_to_dict(tmpl, session.get(TemplateVersion, version_id))


def upload_file_version(
    owner_username: str,
    *,
    name: str,
    template_type: str,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    safe_filename = Path(filename).name
    normalized_type = _validate_file_template_type(template_type, safe_filename)
    if not data:
        raise ValueError("Файл шаблона пуст")

    version_id = _new_id()
    resolved_template_id = template_id or _new_id()
    storage_key = f"template-library/{resolved_template_id}/{version_id}/{safe_filename}"
    try:
        variables = _extract_variables(_file_text(safe_filename, data))
    except Exception as exc:
        raise ValueError("Не удалось прочитать содержимое шаблона") from exc
    put_bytes(storage_key, data, content_type=content_type)

    try:
        with session_scope() as session:
            if template_id:
                tmpl = session.get(MailTemplate, template_id)
                if tmpl is None or tmpl.owner_username != owner_username:
                    raise FileNotFoundError("Шаблон не найден")
                if tmpl.template_type != normalized_type:
                    raise ValueError("Тип загружаемого файла не совпадает с типом шаблона")
                current = session.get(TemplateVersion, tmpl.active_version_id) if tmpl.active_version_id else None
                version_number = (current.version_number + 1) if current else 1
                if name:
                    tmpl.name = name
            else:
                version_number = 1
                tmpl = MailTemplate(
                    id=resolved_template_id,
                    owner_username=owner_username,
                    name=name or Path(safe_filename).stem or "Шаблон",
                    template_type=normalized_type,
                    status="ready",
                    active_version_id=version_id,
                    tags=[],
                )
                session.add(tmpl)

            version = TemplateVersion(
                id=version_id,
                template_id=resolved_template_id,
                version_number=version_number,
                subject="",
                body_html="",
                body_text="",
                variables=variables,
                storage_key=storage_key,
                filename=safe_filename,
                created_by=owner_username,
            )
            session.add(version)
            tmpl.active_version_id = version_id
            tmpl.status = "ready"
            tmpl.archived = False
            tmpl.updated_at = _now()
            session.flush()
            return template_to_dict(tmpl, version)
    except BaseException:
        delete_object(storage_key)
        raise


def get_template_file(template_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username or not tmpl.active_version_id:
            return None
        version = session.get(TemplateVersion, tmpl.active_version_id)
        if version is None or not version.storage_key or not version.filename:
            return None
        storage_key = version.storage_key
        filename = version.filename
        template_type = tmpl.template_type
    return {
        "content": get_bytes(storage_key),
        "filename": filename,
        "template_type": template_type,
        "storage_key": storage_key,
    }


def build_file_preview(template_id: str, owner_username: str) -> dict[str, Any] | None:
    item = get_template_file(template_id, owner_username)
    if item is None:
        return None
    data = item["content"]
    filename = str(item["filename"])
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return {"content": data, "filename": f"{Path(filename).stem}.pdf", "media_type": "application/pdf"}

    with TemporaryDirectory(prefix="template-preview-") as temp_dir:
        root = Path(temp_dir)
        preview_path = root / "preview.pdf"
        if suffix == ".docx":
            source_path = root / filename
            source_path.write_bytes(data)
            from src.generator.generation.template_preview import _convert_preview_docx_to_pdf

            converted = _convert_preview_docx_to_pdf(source_path, root / "converted")
        elif suffix in {".html", ".htm"}:
            from src.generator.generation.pdf_converter import convert_html_to_pdf

            converted = convert_html_to_pdf(_decode_text(data), preview_path, filename=filename)
        else:
            converted = None
        if converted is None or not converted.exists():
            raise RuntimeError("Не удалось сформировать PDF-предпросмотр шаблона")
        return {
            "content": converted.read_bytes(),
            "filename": f"{Path(filename).stem}.pdf",
            "media_type": "application/pdf",
        }

def list_templates(
    owner_username: str,
    *,
    template_type: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(MailTemplate).where(MailTemplate.owner_username == owner_username)
        if not include_archived:
            stmt = stmt.where(MailTemplate.archived.is_(False))
        if template_type:
            stmt = stmt.where(MailTemplate.template_type == template_type)
        if q:
            stmt = stmt.where(MailTemplate.name.ilike(f"%{q.strip()}%"))
        rows = session.scalars(stmt.order_by(MailTemplate.updated_at.desc())).all()
        result = []
        for row in rows:
            version = session.get(TemplateVersion, row.active_version_id) if row.active_version_id else None
            result.append(template_to_dict(row, version))
        return result


def get_template(template_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(MailTemplate, template_id)
        if row is None or row.owner_username != owner_username:
            return None
        version = session.get(TemplateVersion, row.active_version_id) if row.active_version_id else None
        return template_to_dict(row, version)


def save_version(
    template_id: str,
    owner_username: str,
    *,
    subject: str | None = None,
    body_html: str | None = None,
    body_text: str | None = None,
    variables: list[dict[str, Any]] | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            return None
        current = session.get(TemplateVersion, tmpl.active_version_id) if tmpl.active_version_id else None
        next_number = (current.version_number + 1) if current else 1
        subj = subject if subject is not None else (current.subject if current else "")
        html = body_html if body_html is not None else (current.body_html if current else "")
        text = body_text if body_text is not None else (current.body_text if current else "")
        vars_list = variables if variables is not None else _extract_variables(f"{subj}\n{html}\n{text}")
        version_id = _new_id()
        session.add(
            TemplateVersion(
                id=version_id,
                template_id=template_id,
                version_number=next_number,
                subject=subj,
                body_html=html,
                body_text=text,
                variables=vars_list,
                storage_key=current.storage_key if current else None,
                filename=current.filename if current else None,
                created_by=owner_username,
            )
        )
        tmpl.active_version_id = version_id
        tmpl.status = "ready"
        if name is not None:
            tmpl.name = name
        tmpl.updated_at = _now()
        session.flush()
        return template_to_dict(tmpl, session.get(TemplateVersion, version_id))


def duplicate_template(template_id: str, owner_username: str) -> dict[str, Any] | None:
    source = get_template(template_id, owner_username)
    if not source:
        return None
    version = source.get("version") or {}
    storage_key = str(version.get("storage_key") or "")
    filename = str(version.get("filename") or "")
    if storage_key and filename:
        file_item = get_template_file(template_id, owner_username)
        if file_item is None:
            return None
        return upload_file_version(
            owner_username,
            name=f"{source['name']} (копия)",
            template_type=source["template_type"],
            filename=filename,
            data=file_item["content"],
            content_type=None,
        )
    return create_template(
        owner_username,
        name=f"{source['name']} (копия)",
        template_type=source["template_type"],
        subject=str(version.get("subject") or ""),
        body_html=str(version.get("body_html") or ""),
        body_text=str(version.get("body_text") or ""),
        tags=list(source.get("tags") or []),
    )


def archive_template(template_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            return None
        tmpl.archived = True
        tmpl.updated_at = _now()
        session.flush()
        return template_to_dict(tmpl)


def list_versions(template_id: str, owner_username: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            return []
        rows = session.scalars(
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(TemplateVersion.version_number.desc())
        ).all()
        return [version_to_dict(r) for r in rows]


def preview_template(template_id: str, owner_username: str, sample: dict[str, Any] | None = None) -> dict[str, Any] | None:
    tmpl = get_template(template_id, owner_username)
    if not tmpl:
        return None
    version = tmpl.get("version") or {}
    sample = sample or {
        "company": "ООО Пример",
        "contact_name": "Иван Иванов",
        "email": "ivan@example.com",
        "region": "Москва",
        "campaign_name": "Тестовая рассылка",
    }
    html = str(version.get("body_html") or "")
    subject = str(version.get("subject") or "")
    for key, value in sample.items():
        html = html.replace("{{" + key + "}}", str(value))
        subject = subject.replace("{{" + key + "}}", str(value))
    return {"subject": subject, "body_html": html, "sample": sample}
