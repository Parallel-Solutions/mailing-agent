from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import TemplateVersion
from src.infra.object_store import get_bytes
from src.utils.logger import logger


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_template_text_extraction(kwargs: dict[str, Any]) -> None:
    """Populate the persistent source-text cache for one immutable template version."""
    version_id = str(kwargs.get("version_id") or "").strip()
    if not version_id:
        raise ValueError("version_id is required")

    with session_scope() as session:
        version = session.get(TemplateVersion, version_id)
        if version is None:
            return
        if version.source_text is not None:
            if version.text_extraction_status != "ready":
                version.text_extraction_status = "ready"
                version.text_extraction_error = None
                version.text_extracted_at = version.text_extracted_at or _now()
            return
        storage_key = str(version.storage_key or "")
        filename = str(version.filename or "")

    if not storage_key or not filename:
        mark_template_text_extraction_failed(
            version_id, "Template source file is unavailable"
        )
        return

    try:
        from src.campaigns import template_service

        source_data = get_bytes(storage_key)
        source_text = template_service._file_text(filename, source_data)  # noqa: SLF001
        source_sha256 = template_service._content_sha256(source_data)  # noqa: SLF001
        with session_scope() as session:
            version = session.get(TemplateVersion, version_id)
            if version is None or version.source_text is not None:
                return
            version.source_text = source_text
            version.source_sha256 = source_sha256
            version.text_extraction_status = "ready"
            version.text_extraction_error = None
            version.text_extracted_at = _now()
        logger.info(
            "template_source_text_extracted",
            template_version_id=version_id,
            filename=filename,
            size_bytes=len(source_data),
            text_length=len(source_text),
        )
    except Exception as exc:
        with session_scope() as session:
            version = session.get(TemplateVersion, version_id)
            if version is not None and version.source_text is None:
                # Keep the version pending while the queue retries the task.
                version.text_extraction_error = str(exc)[:2000]
        raise


def mark_template_text_extraction_failed(version_id: str, message: str) -> None:
    with session_scope() as session:
        version = session.get(TemplateVersion, str(version_id))
        if version is None or version.source_text is not None:
            return
        version.text_extraction_status = "failed"
        version.text_extraction_error = str(
            message or "Template text extraction failed"
        )[:2000]
        version.text_extracted_at = _now()


def enqueue_pending_template_text_extractions(*, limit: int = 10) -> int:
    """Enqueue missing caches; active_key makes this safe across worker replicas."""
    from src.workers.task_queue import enqueue_task

    with session_scope() as session:
        pending = session.execute(
            select(
                TemplateVersion.id,
                TemplateVersion.template_id,
                TemplateVersion.created_by,
            )
            .where(
                TemplateVersion.storage_key.is_not(None),
                TemplateVersion.source_text.is_(None),
                TemplateVersion.text_extraction_status == "pending",
            )
            .order_by(TemplateVersion.created_at.asc())
            .limit(max(1, int(limit)))
        ).all()

    created_count = 0
    for version_id, template_id, created_by in pending:
        task, created = enqueue_task(
            task_type="template_text_extract",
            job_id=str(template_id),
            owner_username=str(created_by or ""),
            payload={"version_id": str(version_id)},
            priority=-10,
            max_attempts=3,
            active_key=f"template_text_extract:{version_id}",
        )
        if str(task.get("status") or "") in {"queued", "running", "retry"}:
            with session_scope() as session:
                version = session.get(TemplateVersion, str(version_id))
                if (
                    version is not None
                    and version.source_text is None
                    and version.text_extraction_status == "pending"
                ):
                    version.text_extraction_status = "queued"
        created_count += int(created)
    return created_count
