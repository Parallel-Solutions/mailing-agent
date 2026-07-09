from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import (
    DATA_XLSX_PATH,
    START_OUTGOING_NUMBER,
    TEMPLATE_PREVIEW_AUTO_FIT_ONE_PAGE,
    TEMPLATE_PREVIEW_NORMALIZE_DOCX,
    TEMPLATE_PREVIEW_PDF_RENDERER,
)
from src.generator.generation.excel_io import load_rows
from src.generator.generation.generator_agent import finalize_generated_files, process_generator_row
from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, document_mode_kinds, normalize_document_mode
from src.generator.generation.docxjs_converter import convert_docx_to_pdf_with_docxjs
from src.generator.generation.docx_preview_normalizer import normalize_docx_for_preview
from src.generator.generation.pdf_converter import convert_docx_batch
from src.generator.generation.pdf_quality import validate_kp_pdf
from src.generator.generation.template_visual_audit import audit_template_preview_document
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type
from src.jobs import resolve_job_paths


PREVIEW_STATE_FILENAME = "template_preview.json"
PREVIEW_APPROVAL_PENDING = "pending"
PREVIEW_APPROVAL_APPROVED = "approved"
PREVIEW_APPROVAL_REJECTED = "rejected"


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _preview_root(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / "template_preview"


def _preview_state_path(job_id: str | None) -> Path:
    return _preview_root(job_id) / PREVIEW_STATE_FILENAME


def _load_source_rows(job_id: str | None) -> tuple[Path, list[dict]]:
    job_paths = resolve_job_paths(job_id)
    source_path = job_paths.data_xlsx if job_paths.data_xlsx.exists() else DATA_XLSX_PATH
    if not source_path.exists():
        raise FileNotFoundError("data.xlsx не найден")

    workbook, _, rows = load_rows(source_path)
    close = getattr(workbook, "close", None)
    if callable(close):
        close()
    if not rows:
        raise ValueError("В таблице нет строк для предпросмотра.")
    return source_path, rows


def _pick_preview_row(rows: list[dict], row_id: str | None) -> tuple[int, dict]:
    requested_row_id = _safe_str(row_id)
    if requested_row_id:
        for index, row in enumerate(rows):
            if _safe_str(row.get("ID")) == requested_row_id:
                return index, row
        raise ValueError(f"Строка {requested_row_id} не найдена в таблице.")
    return 0, rows[0]


def _row_label(row: dict) -> str:
    for key in ("ADM_NAME", "MUN_NAME", "MUN_R_NAME", "SUB_RF"):
        value = _safe_str(row.get(key))
        if value:
            return value
    row_id = _safe_str(row.get("ID"))
    return f"строка {row_id}" if row_id else "первая строка"


def _first_existing_file(result_files: dict[str, Any], suffix: str) -> Path | None:
    for key in ("kp_final_pdf", "kp_final_docx", "contract_final_docx", "kp", "contract"):
        value = result_files.get(key)
        if not value:
            continue
        path = Path(value)
        if path.suffix.lower() == suffix and path.exists():
            return path
    for value in result_files.values():
        if not value:
            continue
        path = Path(value)
        if path.suffix.lower() == suffix and path.exists():
            return path
    return None


def _save_preview_state(job_id: str | None, payload: dict[str, Any]) -> None:
    path = _preview_state_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_template_preview_state(job_id: str | None) -> dict[str, Any]:
    path = _preview_state_path(job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_template_preview_state(job_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    _save_preview_state(job_id, payload)
    return payload


def mark_template_preview_approval(
    job_id: str | None,
    *,
    approved: bool,
    reason: str = "",
    actor: str = "chat",
) -> dict[str, Any]:
    state = load_template_preview_state(job_id)
    if not state:
        raise FileNotFoundError("Предпросмотр шаблона ещё не собран.")
    state["approval_status"] = PREVIEW_APPROVAL_APPROVED if approved else PREVIEW_APPROVAL_REJECTED
    state["approval_reason"] = _safe_str(reason)
    state["approved_by"] = _safe_str(actor)
    state["approved_at"] = datetime.now().isoformat(timespec="seconds") if approved else ""
    state["rejected_at"] = "" if approved else datetime.now().isoformat(timespec="seconds")
    return save_template_preview_state(job_id, state)


def is_template_preview_approved(
    job_id: str | None,
    *,
    document_mode: str | None = None,
    work_type: str | None = None,
) -> bool:
    state = load_template_preview_state(job_id)
    if state.get("status") != "ready":
        return False
    if state.get("approval_status") != PREVIEW_APPROVAL_APPROVED:
        return False
    if document_mode and normalize_document_mode(state.get("document_mode") or "") != normalize_document_mode(document_mode):
        return False
    if work_type and normalize_work_type(state.get("work_type") or "") != normalize_work_type(work_type):
        return False
    return True


def resolve_template_preview_file(job_id: str | None, kind: str) -> Path:
    state = load_template_preview_state(job_id)
    key = "pdf_path" if str(kind or "").lower() == "pdf" else "docx_path"
    raw_path = _safe_str(state.get(key))
    if not raw_path:
        raise FileNotFoundError("Файл предпросмотра не найден.")
    path = Path(raw_path)
    preview_root = _preview_root(job_id).resolve()
    try:
        resolved = path.resolve()
        resolved.relative_to(preview_root)
    except Exception as exc:
        raise FileNotFoundError("Файл предпросмотра не найден.") from exc
    if not resolved.is_file():
        raise FileNotFoundError("Файл предпросмотра не найден.")
    return resolved

def _convert_preview_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path | None:
    result = convert_docx_batch([docx_path], output_dir, chunk_size=1, worker_count=1)
    pdf_path = result.get(docx_path)
    return pdf_path if pdf_path is not None and pdf_path.exists() else None


def build_template_preview(
    *,
    job_id: str | None,
    document_mode: str | None = None,
    work_type: str | None = None,
    row_id: str | None = None,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    document_mode = normalize_document_mode(document_mode or DOCUMENT_MODE_BOTH)
    work_type = normalize_work_type(work_type or DEFAULT_WORK_TYPE)
    _, rows = _load_source_rows(job_id)
    row_index, row = _pick_preview_row(rows, row_id)

    preview_root = _preview_root(job_id)
    if preview_root.exists():
        shutil.rmtree(preview_root, ignore_errors=True)
    output_dir = preview_root / "output"
    batch_docx_dir = preview_root / "_batch_docx"
    batch_pdf_dir = preview_root / "_batch_pdf"
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_docx_dir.mkdir(parents=True, exist_ok=True)
    batch_pdf_dir.mkdir(parents=True, exist_ok=True)

    result = process_generator_row(
        (0, START_OUTGOING_NUMBER + row_index, row),
        output_dir=output_dir,
        batch_docx_dir=batch_docx_dir,
        templates_dir=None if job_paths.uses_legacy_layout else job_paths.templates_dir,
        document_mode=document_mode,
        work_type=work_type,
    )
    results = [result]
    finalize_generated_files(
        results,
        batch_pdf_dir=batch_pdf_dir,
        templates_dir=None if job_paths.uses_legacy_layout else job_paths.templates_dir,
        create_pdf=True,
        chunk_size=1,
        worker_count=1,
    )

    result_payload = results[0]
    result_files = result_payload.get("files") or {}
    pdf_path = _first_existing_file(result_files, ".pdf")
    docx_path = _first_existing_file(result_files, ".docx")
    source_docx_path = docx_path
    normalization_report: dict[str, Any] = {}
    pdf_quality: dict[str, Any] = {}
    pdf_renderer = "default"

    if docx_path is not None and TEMPLATE_PREVIEW_NORMALIZE_DOCX:
        normalized_docx = preview_root / "template-preview-normalized.docx"
        report = normalize_docx_for_preview(docx_path, normalized_docx)
        normalization_report = report.to_dict()
        if normalized_docx.exists():
            docx_path = normalized_docx

    if docx_path is not None and TEMPLATE_PREVIEW_PDF_RENDERER == "docxjs":
        docxjs_pdf = convert_docx_to_pdf_with_docxjs(docx_path, preview_root / "template-preview-docxjs.pdf")
        if docxjs_pdf is not None:
            pdf_path = docxjs_pdf
            pdf_renderer = "docxjs"
            pdf_quality = validate_kp_pdf(pdf_path) if "kp" in document_mode_kinds(document_mode) else {}

        if (
            pdf_path is not None
            and TEMPLATE_PREVIEW_AUTO_FIT_ONE_PAGE
            and "kp" in document_mode_kinds(document_mode)
            and pdf_quality
            and not pdf_quality.get("ok")
            and pdf_quality.get("reason") == "page_count"
        ):
            for half_points in (20, 19, 18, 17, 16, 15, 14):
                compact_docx = preview_root / f"template-preview-fit-{half_points}.docx"
                compact_pdf = preview_root / f"template-preview-fit-{half_points}.pdf"
                report = normalize_docx_for_preview(
                    docx_path,
                    compact_docx,
                    compact_body=True,
                    max_body_font_half_points=half_points,
                )
                fitted_pdf = convert_docx_to_pdf_with_docxjs(compact_docx, compact_pdf)
                if fitted_pdf is None:
                    continue
                fitted_quality = validate_kp_pdf(fitted_pdf)
                docx_path = compact_docx
                pdf_path = fitted_pdf
                pdf_quality = fitted_quality
                pdf_renderer = f"docxjs_fit_{half_points}"
                normalization_report = report.to_dict()
                if fitted_quality.get("ok"):
                    break
        elif docxjs_pdf is None and pdf_path is not None:
            fallback_pdf = _convert_preview_docx_to_pdf(docx_path, preview_root / "template-preview-gotenberg")
            if fallback_pdf is not None:
                pdf_path = fallback_pdf
                pdf_renderer = "gotenberg_normalized_fallback"
                pdf_quality = validate_kp_pdf(pdf_path) if "kp" in document_mode_kinds(document_mode) else {}
            else:
                pdf_renderer = "default_fallback"

    if pdf_path is None and "kp" in document_mode_kinds(document_mode):
        error = str(result_payload.get("error") or "Не удалось собрать PDF-пример КП.").strip()
        raise RuntimeError(error)
    if pdf_path is None and docx_path is None:
        raise RuntimeError("Не удалось собрать пример документа.")

    if pdf_path is not None and not pdf_quality and "kp" in document_mode_kinds(document_mode):
        pdf_quality = validate_kp_pdf(pdf_path)

    visual_audit: dict[str, Any] = {}
    if docx_path is not None and pdf_path is not None and "kp" in document_mode_kinds(document_mode):
        template_path = job_paths.templates_dir / "kp_template.docx"
        visual_audit = audit_template_preview_document(
            docx_path=docx_path,
            pdf_path=pdf_path,
            output_dir=preview_root / "visual_audit",
            document_mode=document_mode,
            template_docx=template_path if template_path.exists() else None,
        )
        audited_pdf_raw = _safe_str(visual_audit.get("final_pdf_path"))
        audited_docx_raw = _safe_str(visual_audit.get("final_docx_path"))
        audited_pdf = Path(audited_pdf_raw) if audited_pdf_raw else None
        audited_docx = Path(audited_docx_raw) if audited_docx_raw else None
        if audited_pdf is not None and audited_pdf.is_file():
            pdf_path = audited_pdf
        if audited_docx is not None and audited_docx.is_file():
            docx_path = audited_docx
        if isinstance(visual_audit.get("pdf_quality_after"), dict):
            pdf_quality = visual_audit["pdf_quality_after"]

    preview_status = "ready"
    approval_status = PREVIEW_APPROVAL_PENDING
    approval_reason = ""
    failed_message = ""
    if "kp" in document_mode_kinds(document_mode):
        quality_failed = bool(pdf_quality) and not bool(pdf_quality.get("ok"))
        visual_failed = isinstance(visual_audit, dict) and visual_audit.get("ok") is False
        if quality_failed or visual_failed:
            preview_status = "failed_quality"
            approval_status = PREVIEW_APPROVAL_REJECTED
            quality_message = _safe_str(pdf_quality.get("message") if isinstance(pdf_quality, dict) else "")
            issues = visual_audit.get("issues") if isinstance(visual_audit, dict) and isinstance(visual_audit.get("issues"), list) else []
            issue_message = "; ".join(_safe_str(item) for item in issues[:3] if _safe_str(item))
            failed_message = quality_message or issue_message or "KP preview failed quality check. Mass generation is blocked."
            approval_reason = failed_message

    payload = {
        "status": preview_status,
        "job_id": job_id or "",
        "row_id": _safe_str(row.get("ID")),
        "row_label": _row_label(row),
        "row_number": row_index + 1,
        "document_mode": document_mode,
        "work_type": work_type,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "approval_status": approval_status,
        "approval_reason": approval_reason,
        "approved_by": "",
        "approved_at": "",
        "rejected_at": datetime.now().isoformat(timespec="seconds") if preview_status == "failed_quality" else "",
        "pdf_path": str(pdf_path) if pdf_path else "",
        "docx_path": str(docx_path) if docx_path else "",
        "source_docx_path": str(source_docx_path) if source_docx_path else "",
        "has_pdf": pdf_path is not None and pdf_path.is_file(),
        "has_docx": docx_path is not None and docx_path.is_file(),
        "pdf_renderer": pdf_renderer,
        "normalization_report": normalization_report,
        "pdf_quality": pdf_quality,
        "visual_audit": visual_audit,
    }
    _save_preview_state(job_id, payload)
    if preview_status == "failed_quality":
        raise ValueError(failed_message)
    return payload
