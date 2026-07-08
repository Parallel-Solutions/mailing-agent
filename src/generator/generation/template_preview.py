from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import DATA_XLSX_PATH, START_OUTGOING_NUMBER
from src.generator.generation.excel_io import load_rows
from src.generator.generation.generator_agent import finalize_generated_files, process_generator_row
from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, normalize_document_mode
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type
from src.jobs import resolve_job_paths


PREVIEW_STATE_FILENAME = "template_preview.json"


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


def resolve_template_preview_file(job_id: str | None, kind: str) -> Path:
    state = load_template_preview_state(job_id)
    key = "pdf_path" if str(kind or "").lower() == "pdf" else "docx_path"
    path = Path(_safe_str(state.get(key)))
    preview_root = _preview_root(job_id).resolve()
    try:
        resolved = path.resolve()
        resolved.relative_to(preview_root)
    except Exception as exc:
        raise FileNotFoundError("Файл предпросмотра не найден.") from exc
    if not resolved.exists():
        raise FileNotFoundError("Файл предпросмотра не найден.")
    return resolved


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

    result_files = results[0].get("files") or {}
    pdf_path = _first_existing_file(result_files, ".pdf")
    docx_path = _first_existing_file(result_files, ".docx")
    if pdf_path is None and docx_path is None:
        raise RuntimeError("Не удалось собрать пример документа.")

    payload = {
        "status": "ready",
        "job_id": job_id or "",
        "row_id": _safe_str(row.get("ID")),
        "row_label": _row_label(row),
        "row_number": row_index + 1,
        "document_mode": document_mode,
        "work_type": work_type,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pdf_path": str(pdf_path) if pdf_path else "",
        "docx_path": str(docx_path) if docx_path else "",
        "has_pdf": pdf_path is not None,
        "has_docx": docx_path is not None,
    }
    _save_preview_state(job_id, payload)
    return payload
