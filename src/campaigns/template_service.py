"""Mail / document templates with versions."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from docx import Document
from pypdf import PdfReader
from sqlalchemy import or_, select

from src.infra.db import session_scope
from src.infra.models import MailTemplate, TemplateVersion
from src.infra.object_store import delete as delete_object
from src.infra.object_store import get_bytes, put_bytes

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
FILE_TEMPLATE_TYPE_ALIASES = {
    "kp": "document",
    "contract": "document",
}
FILE_TEMPLATE_EXTENSIONS = {
    "document": {".docx", ".pdf", ".html", ".htm"},
}
LEGACY_DOCUMENT_TYPES = ("kp", "contract")


def normalize_file_template_type(template_type: str) -> str:
    normalized = str(template_type or "").strip().lower()
    return FILE_TEMPLATE_TYPE_ALIASES.get(normalized, normalized)


def normalize_template_type_filter(template_type: str | None) -> str | None:
    if not template_type:
        return None
    return normalize_file_template_type(template_type)

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
    if suffix in {".html", ".htm", ".txt"}:
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
    normalized_type = normalize_file_template_type(template_type)
    allowed = FILE_TEMPLATE_EXTENSIONS.get(normalized_type)
    if not allowed:
        raise ValueError("Файловая загрузка доступна только для документов")
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        formats = ", ".join(sorted(allowed))
        raise ValueError(f"Для этого типа шаблона доступны форматы: {formats}")
    return normalized_type


def _template_types_compatible(existing: str, incoming: str) -> bool:
    return normalize_file_template_type(existing) == normalize_file_template_type(incoming)

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
        "rendered_pdf_storage_key": row.rendered_pdf_storage_key,
        "rendered_pdf_filename": row.rendered_pdf_filename,
        "editor_state": row.editor_state,
        "artifacts": {
            "source": {"filename": row.filename, "storage_key": row.storage_key} if row.filename else None,
            "delivery_pdf": {
                "filename": row.rendered_pdf_filename,
                "storage_key": row.rendered_pdf_storage_key,
            } if row.rendered_pdf_filename else None,
        },
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


def _build_kp_pdf_artifact(filename: str, data: bytes) -> tuple[bytes, str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return data, f"{Path(filename).stem}.pdf"
    if suffix != ".docx":
        raise ValueError("Исходником КП должен быть DOCX или PDF")
    with TemporaryDirectory(prefix="kp-template-pdf-") as temp_dir:
        root = Path(temp_dir)
        source_path = root / Path(filename).name
        source_path.write_bytes(data)
        from src.generator.generation.template_preview import _convert_preview_docx_to_pdf

        converted = _convert_preview_docx_to_pdf(source_path, root / "converted")
        if converted is None or not converted.exists():
            raise RuntimeError("Не удалось создать PDF-копию исходного DOCX")
        return converted.read_bytes(), f"{Path(filename).stem}.pdf"


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

    try:
        variables = _extract_variables(_file_text(safe_filename, data))
        rendered_pdf_data: bytes | None = None
        rendered_pdf_filename: str | None = None
        if normalized_type == "kp":
            rendered_pdf_data, rendered_pdf_filename = _build_kp_pdf_artifact(safe_filename, data)
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise ValueError("Не удалось прочитать содержимое шаблона") from exc

    version_id = _new_id()
    resolved_template_id = template_id or _new_id()
    storage_key = f"template-library/{resolved_template_id}/{version_id}/source/{safe_filename}"
    rendered_pdf_storage_key = None
    if rendered_pdf_data is not None and rendered_pdf_filename:
        rendered_pdf_storage_key = (
            storage_key
            if Path(safe_filename).suffix.lower() == ".pdf"
            else f"template-library/{resolved_template_id}/{version_id}/delivery/{rendered_pdf_filename}"
        )

    stored_keys: list[str] = []
    try:
        put_bytes(storage_key, data, content_type=content_type)
        stored_keys.append(storage_key)
        if rendered_pdf_storage_key and rendered_pdf_storage_key != storage_key and rendered_pdf_data is not None:
            put_bytes(rendered_pdf_storage_key, rendered_pdf_data, content_type="application/pdf")
            stored_keys.append(rendered_pdf_storage_key)

        with session_scope() as session:
            if template_id:
                tmpl = session.get(MailTemplate, template_id)
                if tmpl is None or tmpl.owner_username != owner_username:
                    raise FileNotFoundError("Шаблон не найден")
                if not _template_types_compatible(tmpl.template_type, normalized_type):
                    raise ValueError("Тип загружаемого файла не совпадает с типом шаблона")
                if tmpl.template_type in LEGACY_DOCUMENT_TYPES:
                    tmpl.template_type = "document"
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
                rendered_pdf_storage_key=rendered_pdf_storage_key,
                rendered_pdf_filename=rendered_pdf_filename,
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
        for key in reversed(stored_keys):
            delete_object(key)
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


def get_template_delivery_file(template_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username or not tmpl.active_version_id:
            return None
        version = session.get(TemplateVersion, tmpl.active_version_id)
        if version is None:
            return None
        version_id = version.id
        rendered_key = version.rendered_pdf_storage_key
        rendered_name = version.rendered_pdf_filename
        source_key = version.storage_key
        source_name = version.filename
        template_type = tmpl.template_type
    if rendered_key and rendered_name:
        return {
            "content": get_bytes(rendered_key),
            "filename": rendered_name,
            "media_type": "application/pdf",
            "template_type": template_type,
        }
    if template_type != "kp" or not source_key or not source_name:
        return None

    source_data = get_bytes(source_key)
    suffix = Path(source_name).suffix.lower()
    if suffix == ".pdf":
        pdf_data = source_data
        pdf_name = f"{Path(source_name).stem}.pdf"
        pdf_key = source_key
    elif suffix == ".docx":
        pdf_data, pdf_name = _build_kp_pdf_artifact(source_name, source_data)
        pdf_key = f"template-library/{template_id}/{version_id}/delivery/{pdf_name}"
        put_bytes(pdf_key, pdf_data, content_type="application/pdf")
    else:
        return None

    with session_scope() as session:
        current = session.get(TemplateVersion, version_id)
        if current is not None and current.template_id == template_id:
            current.rendered_pdf_storage_key = pdf_key
            current.rendered_pdf_filename = pdf_name
            session.flush()
    return {
        "content": pdf_data,
        "filename": pdf_name,
        "media_type": "application/pdf",
        "template_type": template_type,
    }


def get_template_version_file(template_id: str, version_id: str, owner_username: str) -> dict[str, Any]:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            raise FileNotFoundError("Шаблон не найден")
        version = session.get(TemplateVersion, version_id)
        if version is None or version.template_id != template_id or not version.storage_key or not version.filename:
            raise FileNotFoundError("Версия документа не найдена")
        storage_key = version.storage_key
        filename = version.filename
    return {
        "content": get_bytes(storage_key),
        "filename": filename,
        "media_type": (
            "application/pdf"
            if Path(filename).suffix.lower() == ".pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    }


def _render_kp_html_pdf_bytes(body_html: str, *, sample_values: bool) -> bytes:
    html = str(body_html or "").strip()
    if not html:
        raise ValueError("В шаблоне КП отсутствует редактируемый макет")
    if sample_values:
        from src.generator.templates.certification import certification_context

        names = tuple(sorted(set(VARIABLE_RE.findall(html))))
        values = certification_context(names, "normal")
        html = VARIABLE_RE.sub(lambda match: escape(values.get(match.group(1), match.group(0))), html)
    with TemporaryDirectory(prefix="kp-editor-render-") as temp_dir:
        output_path = Path(temp_dir) / "preview.pdf"
        from src.generator.generation.pdf_converter import convert_html_to_pdf

        converted = convert_html_to_pdf(html, output_path, filename="index.html")
        if converted is None or not converted.exists():
            raise RuntimeError("Не удалось собрать PDF из макета КП")
        return converted.read_bytes()


def build_kp_pdf_preview(
    template_id: str,
    owner_username: str,
    *,
    body_html: str | None = None,
) -> dict[str, Any]:
    template = get_template(template_id, owner_username)
    if not template or template.get("template_type") != "kp":
        raise FileNotFoundError("Шаблон КП не найден")
    version = template.get("version") or {}
    html = body_html if body_html is not None else str(version.get("body_html") or "")
    return {
        "content": _render_kp_html_pdf_bytes(html, sample_values=True),
        "filename": f"{template['name']}_preview.pdf",
        "media_type": "application/pdf",
    }


def save_kp_html_version(
    template_id: str,
    owner_username: str,
    *,
    body_html: str,
    name: str | None = None,
) -> dict[str, Any]:
    template = get_template(template_id, owner_username)
    if not template or template.get("template_type") != "kp":
        raise FileNotFoundError("Шаблон КП не найден")
    html = str(body_html or "").strip()
    if not html:
        raise ValueError("Макет КП не может быть пустым")
    pdf_data = _render_kp_html_pdf_bytes(html, sample_values=False)
    version_id = _new_id()
    filename = f"{Path(name or template['name']).stem}.pdf"
    storage_key = f"template-library/{template_id}/{version_id}/{filename}"
    put_bytes(storage_key, pdf_data, content_type="application/pdf")
    try:
        with session_scope() as session:
            tmpl = session.get(MailTemplate, template_id)
            if tmpl is None or tmpl.owner_username != owner_username or tmpl.template_type != "kp":
                raise FileNotFoundError("Шаблон КП не найден")
            current = session.get(TemplateVersion, tmpl.active_version_id) if tmpl.active_version_id else None
            version = TemplateVersion(
                id=version_id,
                template_id=template_id,
                version_number=(current.version_number + 1) if current else 1,
                subject="",
                body_html=html,
                body_text="",
                variables=_extract_variables(html),
                storage_key=storage_key,
                filename=filename,
                rendered_pdf_storage_key=storage_key,
                rendered_pdf_filename=filename,
                created_by=owner_username,
            )
            session.add(version)
            tmpl.active_version_id = version_id
            tmpl.status = "ready"
            tmpl.archived = False
            if name is not None:
                tmpl.name = name
            tmpl.updated_at = _now()
            session.flush()
            return template_to_dict(tmpl, version)
    except BaseException:
        delete_object(storage_key)
        raise


def save_docx_editor_version(template_id: str, owner_username: str, data: bytes) -> dict[str, Any]:
    template = get_template(template_id, owner_username)
    if not template or template.get("template_type") not in {"kp", "contract"}:
        raise FileNotFoundError("Шаблон документа не найден")
    version = template.get("version") or {}
    filename = str(version.get("filename") or f"{template['name']}.docx")
    if not filename.lower().endswith(".docx"):
        raise ValueError("Редактируемый исходник должен иметь формат DOCX")
    Document(BytesIO(data))
    if version.get("storage_key") and get_bytes(str(version["storage_key"])) == data:
        return template
    return upload_file_version(
        owner_username,
        name=str(template["name"]),
        template_type=str(template["template_type"]),
        filename=filename,
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        template_id=template_id,
    )


def build_file_preview(template_id: str, owner_username: str) -> dict[str, Any] | None:
    template = get_template(template_id, owner_username)
    if template and template.get("template_type") == "kp":
        delivery = get_template_delivery_file(template_id, owner_username)
        if delivery is not None:
            return delivery
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
        normalized_filter = normalize_template_type_filter(template_type)
        if normalized_filter == "document":
            stmt = stmt.where(
                or_(
                    MailTemplate.template_type == "document",
                    MailTemplate.template_type.in_(LEGACY_DOCUMENT_TYPES),
                )
            )
        elif normalized_filter:
            stmt = stmt.where(MailTemplate.template_type == normalized_filter)
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
                rendered_pdf_storage_key=current.rendered_pdf_storage_key if current else None,
                rendered_pdf_filename=current.rendered_pdf_filename if current else None,
                editor_state=current.editor_state if current else None,
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
