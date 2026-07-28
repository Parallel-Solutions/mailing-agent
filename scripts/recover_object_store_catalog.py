"""Recover verifiable local catalog rows from surviving MinIO objects.

This tool is intentionally restricted to databases named ``mailing_recovery_*``.
It never deletes or rewrites objects and does not attempt to invent campaigns,
mailboxes, or email-template bodies that cannot be proven from stored data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import boto3
from sqlalchemy import select, text

from src.campaigns import template_service
from src.campaigns.font_service import inspect_font_bytes
from src.infra.db import engine, session_scope
from src.infra.models import FontAsset, MailTemplate, TemplateVersion
from src.utils.config import settings


RECOVERY_WRITE_ENV = "MAILING_AGENT_RECOVERY_ALLOW_WRITE"
TEST_ARTIFACT_START = datetime(2026, 7, 28, 11, 0, tzinfo=timezone.utc)
RECOVERABLE_JOB_ID_RE = re.compile(r"^job-[0-9a-f]{12}$")


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    last_modified: datetime


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def _list_objects() -> list[StoredObject]:
    result: list[StoredObject] = []
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket):
        for item in page.get("Contents") or []:
            result.append(
                StoredObject(
                    key=str(item["Key"]),
                    size=int(item["Size"]),
                    last_modified=item["LastModified"],
                )
            )
    return result


def _get_bytes(key: str) -> bytes:
    response = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    return response["Body"].read()


def _standard_source_parts(item: StoredObject) -> tuple[str, str, str] | None:
    parts = PurePosixPath(item.key).parts
    if len(parts) != 5 or parts[0] != "template-library" or parts[3] != "source":
        return None
    return parts[1], parts[2], parts[4]


def _is_known_test_source(
    item: StoredObject,
    *,
    delivery_sizes: dict[tuple[str, str], list[int]],
) -> bool:
    parts = _standard_source_parts(item)
    if parts is None:
        return False
    template_id, version_id, filename = parts
    if item.last_modified >= TEST_ARTIFACT_START:
        return True
    if filename == "doc.pdf" and item.size <= 1024:
        return True
    if filename == "original.docx" and item.size <= 40_000:
        return True
    if (
        filename == "КП_СТП_районы (1) (1).docx"
        and item.size <= 40_000
        and any(size <= 1024 for size in delivery_sizes.get((template_id, version_id), []))
    ):
        return True
    return False


def _recoverable_document_sources(
    objects: list[StoredObject],
) -> tuple[list[StoredObject], list[StoredObject]]:
    delivery_sizes: dict[tuple[str, str], list[int]] = defaultdict(list)
    for item in objects:
        parts = PurePosixPath(item.key).parts
        if len(parts) == 5 and parts[0] == "template-library" and parts[3] == "delivery":
            delivery_sizes[(parts[1], parts[2])].append(item.size)

    recovered: list[StoredObject] = []
    excluded: list[StoredObject] = []
    for item in objects:
        parts = _standard_source_parts(item)
        if parts is None:
            continue
        suffix = Path(parts[2]).suffix.lower()
        if suffix not in template_service.FILE_TEMPLATE_EXTENSIONS["document"]:
            continue
        if _is_known_test_source(item, delivery_sizes=delivery_sizes):
            excluded.append(item)
        else:
            recovered.append(item)
    return recovered, excluded


def _delivery_by_version(objects: list[StoredObject]) -> dict[tuple[str, str], StoredObject]:
    result: dict[tuple[str, str], StoredObject] = {}
    for item in objects:
        parts = PurePosixPath(item.key).parts
        if (
            len(parts) != 5
            or parts[0] != "template-library"
            or parts[3] != "delivery"
            or Path(parts[4]).suffix.lower() != ".pdf"
        ):
            continue
        signature = (parts[1], parts[2])
        previous = result.get(signature)
        if previous is None or item.last_modified > previous.last_modified:
            result[signature] = item
    return result


def _recover_templates(
    objects: list[StoredObject],
    *,
    owner_username: str,
    apply: bool,
) -> dict[str, Any]:
    sources, excluded = _recoverable_document_sources(objects)
    deliveries = _delivery_by_version(objects)
    all_template_ids = {
        parts[1]
        for item in objects
        if (parts := PurePosixPath(item.key).parts)
        and len(parts) >= 2
        and parts[0] == "template-library"
    }
    all_source_ids = {
        parts[0]
        for item in objects
        if (parts := _standard_source_parts(item)) is not None
    }
    grouped: dict[str, list[StoredObject]] = defaultdict(list)
    for item in sources:
        template_id, _, _ = _standard_source_parts(item) or ("", "", "")
        grouped[template_id].append(item)

    existing_ids: set[str] = set()
    with session_scope() as session:
        if grouped:
            existing_ids = set(
                session.scalars(
                    select(MailTemplate.id).where(MailTemplate.id.in_(tuple(grouped)))
                ).all()
            )

    planned = sorted(template_id for template_id in grouped if template_id not in existing_ids)
    report: dict[str, Any] = {
        "planned_template_ids": planned,
        "existing_template_ids": sorted(existing_ids),
        "excluded_test_keys": sorted(item.key for item in excluded),
        "unrecoverable_without_source_template_ids": sorted(
            all_template_ids - all_source_ids
        ),
        "recovered_versions": 0,
        "errors": [],
    }
    if not apply:
        report["recovered_versions"] = sum(len(grouped[item]) for item in planned)
        return report

    for template_id in planned:
        ordered_sources = sorted(
            grouped[template_id],
            key=lambda item: (item.last_modified, item.key),
        )
        prepared: list[dict[str, Any]] = []
        for version_number, source in enumerate(ordered_sources, start=1):
            _, version_id, filename = _standard_source_parts(source) or ("", "", "")
            try:
                source_data = _get_bytes(source.key)
                source_text = template_service._file_text(filename, source_data)  # noqa: SLF001
                variables = template_service._extract_variables(source_text)  # noqa: SLF001
                extraction_error = None
                extraction_status = "ready"
            except Exception as exc:
                source_data = b""
                source_text = None
                variables = []
                extraction_error = str(exc)[:2000]
                extraction_status = "failed"
                report["errors"].append(
                    {"key": source.key, "error": extraction_error}
                )
            delivery = deliveries.get((template_id, version_id))
            prepared.append(
                {
                    "id": version_id,
                    "version_number": version_number,
                    "filename": filename,
                    "source": source,
                    "source_data": source_data,
                    "source_text": source_text,
                    "variables": variables,
                    "extraction_error": extraction_error,
                    "extraction_status": extraction_status,
                    "delivery": delivery,
                }
            )

        active = prepared[-1]
        created_at = min(item["source"].last_modified for item in prepared)
        updated_at = max(
            max(
                item["source"].last_modified,
                item["delivery"].last_modified if item["delivery"] else item["source"].last_modified,
            )
            for item in prepared
        )
        with session_scope() as session:
            template = MailTemplate(
                id=template_id,
                owner_username=owner_username,
                name=Path(active["filename"]).stem or "Восстановленный шаблон",
                template_type="document",
                status="ready",
                active_version_id=active["id"],
                tags=["recovered"],
                archived=False,
                is_template=bool(active["variables"]),
                attachment_output_format=(
                    "pdf"
                    if active["delivery"] is not None
                    and Path(active["filename"]).suffix.lower() != ".pdf"
                    else "original"
                ),
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(template)
            for item in prepared:
                delivery = item["delivery"]
                source = item["source"]
                source_data = item["source_data"]
                version = TemplateVersion(
                    id=item["id"],
                    template_id=template_id,
                    version_number=item["version_number"],
                    subject="",
                    body_html="",
                    body_text="",
                    variables=item["variables"],
                    storage_key=source.key,
                    filename=item["filename"],
                    rendered_pdf_storage_key=(
                        delivery.key
                        if delivery is not None
                        else source.key
                        if Path(item["filename"]).suffix.lower() == ".pdf"
                        else None
                    ),
                    rendered_pdf_filename=(
                        PurePosixPath(delivery.key).name
                        if delivery is not None
                        else item["filename"]
                        if Path(item["filename"]).suffix.lower() == ".pdf"
                        else None
                    ),
                    editor_state=None,
                    source_text=item["source_text"],
                    source_sha256=(
                        hashlib.sha256(source_data).hexdigest()
                        if source_data
                        else None
                    ),
                    text_extraction_status=item["extraction_status"],
                    text_extraction_error=item["extraction_error"],
                    text_extracted_at=(
                        datetime.now(timezone.utc)
                        if item["extraction_status"] == "ready"
                        else None
                    ),
                    created_by=owner_username,
                    created_at=source.last_modified,
                )
                session.add(version)
                report["recovered_versions"] += 1
    return report


def _recover_fonts(
    objects: list[StoredObject],
    *,
    apply: bool,
) -> dict[str, Any]:
    font_objects = [
        item
        for item in objects
        if len(PurePosixPath(item.key).parts) == 4
        and PurePosixPath(item.key).parts[0] == "fonts"
        and Path(item.key).suffix.lower() in {".ttf", ".otf"}
    ]
    license_keys = {
        str(PurePosixPath(item.key).parent / "license.txt")
        for item in objects
        if PurePosixPath(item.key).name == "license.txt"
    }
    report: dict[str, Any] = {
        "planned_font_keys": [item.key for item in font_objects],
        "recovered": 0,
        "duplicates": [],
        "errors": [],
    }
    if not apply:
        report["recovered"] = len(font_objects)
        return report

    for item in font_objects:
        parts = PurePosixPath(item.key).parts
        owner_username, font_id = parts[1], parts[2]
        with session_scope() as session:
            if session.get(FontAsset, font_id) is not None:
                continue
        try:
            data = _get_bytes(item.key)
            metadata = inspect_font_bytes(PurePosixPath(item.key).name, data)
            digest = hashlib.sha256(data).hexdigest()
            with session_scope() as session:
                duplicate = session.scalar(
                    select(FontAsset).where(
                        FontAsset.owner_username == owner_username,
                        FontAsset.sha256 == digest,
                    )
                )
                if duplicate is not None:
                    report["duplicates"].append(
                        {"key": item.key, "existing_id": duplicate.id}
                    )
                    continue
                license_key = str(PurePosixPath(item.key).parent / "license.txt")
                row = FontAsset(
                    id=font_id,
                    owner_username=owner_username,
                    family=metadata.family,
                    family_normalized=metadata.family_normalized,
                    subfamily=metadata.subfamily,
                    weight=metadata.weight,
                    italic=metadata.italic,
                    postscript_name=metadata.postscript_name,
                    source="recovered",
                    storage_key=item.key,
                    sha256=digest,
                    size_bytes=len(data),
                    original_filename=PurePosixPath(item.key).name,
                    license_type="recovered_existing",
                    license_url="",
                    license_storage_key=license_key if license_key in license_keys else None,
                    embedding_permissions=metadata.embedding_permissions,
                    glyph_coverage=metadata.glyph_coverage,
                    status="active",
                    created_by=owner_username,
                    created_at=item.last_modified,
                    updated_at=item.last_modified,
                )
                session.add(row)
                report["recovered"] += 1
        except Exception as exc:
            report["errors"].append({"key": item.key, "error": str(exc)[:2000]})
    return report


def _recover_missing_job_owners(
    *,
    owner_username: str,
    apply: bool,
) -> dict[str, Any]:
    query = text(
        """
        SELECT DISTINCT source.job_id
        FROM (
            SELECT job_id FROM agent_states
            UNION SELECT job_id FROM clients
            UNION SELECT job_id FROM job_docs
            UNION SELECT job_id FROM job_events
        ) AS source
        LEFT JOIN job_owners AS owners ON owners.job_id = source.job_id
        WHERE owners.job_id IS NULL AND source.job_id IS NOT NULL AND source.job_id <> ''
        ORDER BY source.job_id
        """
    )
    with session_scope() as session:
        discovered = [str(value) for value in session.scalars(query).all()]
    missing = [value for value in discovered if RECOVERABLE_JOB_ID_RE.fullmatch(value)]
    excluded = [value for value in discovered if value not in missing]
    if apply and missing:
        insert = text(
            """
            INSERT INTO job_owners (
                job_id, owner_username, tenant_id, owner_role, created_at, updated_at
            )
            SELECT job_id, :owner, :owner, 'admin', NOW(), NOW()
            FROM unnest(CAST(:job_ids AS text[])) AS job_id
            ON CONFLICT (job_id) DO NOTHING
            """
        )
        with session_scope() as session:
            session.execute(insert, {"owner": owner_username, "job_ids": missing})
    return {
        "missing_job_ids": missing,
        "excluded_nonstandard_job_ids": excluded,
        "recovered": len(missing) if apply else 0,
    }


def _assert_recovery_target(*, apply: bool) -> None:
    database_name = str(engine.url.database or "")
    if not database_name.startswith("mailing_recovery_"):
        raise RuntimeError(
            "Recovery catalog writes are restricted to databases named "
            f"'mailing_recovery_*', got {database_name!r}."
        )
    if apply and os.environ.get(RECOVERY_WRITE_ENV, "").strip() != "1":
        raise RuntimeError(
            f"Set {RECOVERY_WRITE_ENV}=1 to apply recovery changes."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    _assert_recovery_target(apply=args.apply)

    objects = _list_objects()
    report = {
        "database": str(engine.url.database or ""),
        "bucket": settings.s3_bucket,
        "object_count": len(objects),
        "mode": "apply" if args.apply else "dry-run",
        "templates": _recover_templates(
            objects,
            owner_username=args.owner,
            apply=args.apply,
        ),
        "fonts": _recover_fonts(objects, apply=args.apply),
        "job_owners": _recover_missing_job_owners(
            owner_username=args.owner,
            apply=args.apply,
        ),
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
