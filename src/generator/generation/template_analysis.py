from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from src.generator.generation.excel_io import load_rows
from src.generator.generation.template_profile import analyze_docx_style_profile

KP_TEMPLATE_FILENAME = "kp_template_source.docx"
KP_TEMPLATE_PDF_FILENAME = "kp_template_source.pdf"
CONTRACT_TEMPLATE_FILENAME = "contract_template_source.docx"
DOCUMENT_MODE_KP = "kp"
DOCUMENT_MODE_CONTRACT = "contract"
DOCUMENT_MODE_BOTH = "both"


def resolve_job_paths(job_id: str | None):
    from src.jobs.storage import resolve_job_paths as _resolve_job_paths

    return _resolve_job_paths(job_id)

def _document_mode_kinds(value: str | None) -> tuple[str, ...]:
    mode = str(value or DOCUMENT_MODE_BOTH).strip().lower()
    if mode == DOCUMENT_MODE_KP:
        return ("kp",)
    if mode == DOCUMENT_MODE_CONTRACT:
        return ("contract",)
    return ("kp", "contract")


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PLACEHOLDER_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
MAX_TEXT_BLOCKS = 80
MAX_TEXT_CHARS = 6000


def _collapse_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _mtime_label(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _part_label(part_name: str) -> str:
    if part_name == "word/document.xml":
        return "body"
    if "/header" in part_name:
        return "header"
    if "/footer" in part_name:
        return "footer"
    return part_name


def _xml_root(payload: bytes) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None


def _run_style(run: ElementTree.Element) -> dict[str, Any]:
    rpr = run.find(f"{WORD_NS}rPr")
    if rpr is None:
        return {}
    style: dict[str, Any] = {}
    if rpr.find(f"{WORD_NS}b") is not None:
        style["bold"] = True
    if rpr.find(f"{WORD_NS}i") is not None:
        style["italic"] = True
    size = rpr.find(f"{WORD_NS}sz")
    if size is not None:
        value = size.attrib.get(f"{WORD_NS}val")
        if value:
            try:
                style["font_size"] = int(value) / 2
            except ValueError:
                style["font_size"] = value
    highlight = rpr.find(f"{WORD_NS}highlight")
    if highlight is not None:
        value = highlight.attrib.get(f"{WORD_NS}val")
        if value:
            style["highlight"] = value
    return style


def _paragraph_alignment(paragraph: ElementTree.Element) -> str:
    ppr = paragraph.find(f"{WORD_NS}pPr")
    if ppr is None:
        return ""
    jc = ppr.find(f"{WORD_NS}jc")
    if jc is None:
        return ""
    return str(jc.attrib.get(f"{WORD_NS}val") or "")


def _paragraph_runs(paragraph: ElementTree.Element) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    styled_runs: list[dict[str, Any]] = []
    for run in paragraph.findall(f"{WORD_NS}r"):
        run_parts: list[str] = []
        for child in run:
            if child.tag == f"{WORD_NS}t":
                run_parts.append(child.text or "")
            elif child.tag == f"{WORD_NS}tab":
                run_parts.append("\t")
            elif child.tag == f"{WORD_NS}br":
                run_parts.append("\n")
        run_text = "".join(run_parts)
        text_parts.append(run_text)
        clean_run_text = _collapse_text(run_text)
        style = _run_style(run)
        if clean_run_text and style:
            styled_runs.append({"text": clean_run_text[:120], "style": style})
    return _collapse_text("".join(text_parts)), styled_runs[:8]


def _paragraph_block(paragraph: ElementTree.Element, *, part: str, index: int) -> dict[str, Any] | None:
    text, styled_runs = _paragraph_runs(paragraph)
    if not text:
        return None
    block: dict[str, Any] = {
        "part": part,
        "kind": "paragraph",
        "index": index,
        "text": text[:500],
        "placeholders": sorted(set(PLACEHOLDER_RE.findall(text))),
    }
    alignment = _paragraph_alignment(paragraph)
    if alignment:
        block["alignment"] = alignment
    if styled_runs:
        block["styled_runs"] = styled_runs
    return block


def _table_text(table: ElementTree.Element) -> str:
    cells: list[str] = []
    for cell in table.iter(f"{WORD_NS}tc"):
        cell_text = _collapse_text(" ".join((node.text or "") for node in cell.iter(f"{WORD_NS}t")))
        if cell_text:
            cells.append(cell_text)
    return " | ".join(cells)


def _table_block(table: ElementTree.Element, *, part: str, index: int) -> dict[str, Any]:
    rows = list(table.findall(f"{WORD_NS}tr"))
    text = _table_text(table)
    return {
        "part": part,
        "kind": "table",
        "index": index,
        "rows": len(rows),
        "cells": sum(len(row.findall(f"{WORD_NS}tc")) for row in rows),
        "text": text[:700],
        "placeholders": sorted(set(PLACEHOLDER_RE.findall(text))),
    }


def _analyze_docx_template(path: Path, *, kind: str) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    placeholders: set[str] = set()
    image_count = 0
    part_count = 0
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        image_count = sum(1 for name in names if name.startswith("word/media/"))
        part_names = [
            name
            for name in names
            if name == "word/document.xml"
            or re.match(r"word/(header|footer)\d+\.xml$", name)
        ]
        part_count = len(part_names)
        for part_name in part_names:
            root = _xml_root(archive.read(part_name))
            if root is None:
                continue
            part = _part_label(part_name)
            paragraph_index = 0
            table_index = 0
            for element in root.iter():
                if element.tag == f"{WORD_NS}p":
                    paragraph_index += 1
                    block = _paragraph_block(element, part=part, index=paragraph_index)
                    if block:
                        blocks.append(block)
                        placeholders.update(block["placeholders"])
                elif element.tag == f"{WORD_NS}tbl":
                    table_index += 1
                    block = _table_block(element, part=part, index=table_index)
                    blocks.append(block)
                    placeholders.update(block["placeholders"])

    all_text = _collapse_text(" ".join(str(block.get("text") or "") for block in blocks))
    return {
        "kind": kind,
        "filename": path.name,
        "format": "docx",
        "exists": True,
        "size_bytes": path.stat().st_size,
        "updated_at": _mtime_label(path),
        "part_count": part_count,
        "image_count": image_count,
        "table_count": sum(1 for block in blocks if block.get("kind") == "table"),
        "paragraph_count": sum(1 for block in blocks if block.get("kind") == "paragraph"),
        "placeholders": sorted(placeholders),
        "text_preview": all_text[:MAX_TEXT_CHARS],
        "blocks": blocks[:MAX_TEXT_BLOCKS],
        "style_profile": analyze_docx_style_profile(path),
    }


def _analyze_text_template(path: Path, *, kind: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    clean_text = _collapse_text(text)
    return {
        "kind": kind,
        "filename": path.name,
        "format": "txt",
        "exists": True,
        "size_bytes": path.stat().st_size,
        "updated_at": _mtime_label(path),
        "placeholders": sorted(set(PLACEHOLDER_RE.findall(text))),
        "text_preview": clean_text[:MAX_TEXT_CHARS],
        "blocks": [{"part": "body", "kind": "text", "index": 1, "text": clean_text[:700]}] if clean_text else [],
    }


def analyze_template_file(path: Path, *, kind: str) -> dict[str, Any]:
    if not path.exists():
        return {"kind": kind, "filename": path.name, "exists": False}
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            return _analyze_docx_template(path, kind=kind)
        except Exception as exc:
            return {
                "kind": kind,
                "filename": path.name,
                "format": "docx",
                "exists": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
    if suffix == ".txt":
        return _analyze_text_template(path, kind=kind)
    return {
        "kind": kind,
        "filename": path.name,
        "format": suffix.lstrip(".") or "unknown",
        "exists": True,
        "size_bytes": path.stat().st_size,
        "updated_at": _mtime_label(path),
        "note": "This template format is not text-analysed yet.",
    }


def _template_candidates(templates_dir: Path, document_mode: str | None) -> list[tuple[str, Path]]:
    requested_kinds = set(_document_mode_kinds(document_mode))
    candidates: list[tuple[str, Path]] = []
    if "kp" in requested_kinds:
        docx_path = templates_dir / KP_TEMPLATE_FILENAME
        pdf_path = templates_dir / KP_TEMPLATE_PDF_FILENAME
        candidates.append(("kp", docx_path if docx_path.exists() or not pdf_path.exists() else pdf_path))
    if "contract" in requested_kinds:
        candidates.append(("contract", templates_dir / CONTRACT_TEMPLATE_FILENAME))
    mail_docx = templates_dir / "mail_template.docx"
    mail_txt = templates_dir / "mail_template.txt"
    if mail_docx.exists() or mail_txt.exists():
        candidates.append(("mail", mail_docx if mail_docx.exists() else mail_txt))
    return candidates


def _sample_data_context(data_xlsx_path: Path) -> dict[str, Any]:
    if not data_xlsx_path.exists():
        return {"exists": False, "headers": [], "sample_rows": []}
    try:
        _, _, rows = load_rows(data_xlsx_path)
    except Exception as exc:
        return {"exists": True, "error": f"{type(exc).__name__}: {exc}", "headers": [], "sample_rows": []}
    headers: list[str] = []
    if rows:
        headers = [str(key) for key in rows[0].keys() if not str(key).startswith("_")]
    sample_rows = []
    for row in rows[:2]:
        compact = {}
        for key in headers[:24]:
            value = row.get(key)
            if value not in (None, ""):
                compact[key] = _collapse_text(str(value))[:160]
        sample_rows.append(compact)
    return {
        "exists": True,
        "row_count": len(rows),
        "headers": headers[:80],
        "sample_rows": sample_rows,
    }

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "ADM": ("ADM_NAME", "ADM_NAME_1", "Полное название администрации", "Администрация"),
    "ADM_NAME": ("ADM", "ADM_NAME_1", "Полное название администрации", "Администрация"),
    "ADM_NAME_1": ("ADM_NAME", "ADM", "Полное название администрации", "Администрация"),
    "MUN_NAME": ("MUN_NAME_1", "MUN_NAME_2", "Муниципальное образование", "МО"),
    "MUN_NAME_1": ("MUN_NAME", "MUN_NAME_2", "Муниципальное образование", "МО"),
    "MUN_NAME_2": ("MUN_NAME", "MUN_NAME_1", "Муниципальное образование", "МО"),
    "MUN_R_NAME": ("MUN_R_NAME_1", "Муниципальный район", "Район", "Округ"),
    "MUN_R_NAME_1": ("MUN_R_NAME", "Муниципальный район", "Район", "Округ"),
    "SUB_RF": ("Субъект РФ", "Регион", "Область", "Край", "Республика"),
    "HEAD_FIO": ("Глава МО", "Руководитель", "ФИО", "HEAD_FIO_SHORT"),
    "HEAD_FIO_SHORT": ("HEAD_FIO", "Глава МО", "Руководитель", "ФИО"),
    "EMAIL_OSN": ("EMAIL", "E-MAIL", "Эл. Адрес (основной)", "Почта"),
    "EMAIL_DOP": ("Эл. Адрес (доп)", "Доп почта", "Резерв"),
    "TEL_OSN": ("Телефон", "Телефон основной"),
    "TEL_DOP": ("Телефон (доп)", "Доп телефон"),
    "DATE": ("current_date", "CURRENT_DATE", "дата", "Дата документа"),
    "current_date": ("DATE", "CURRENT_DATE", "дата"),
    "VALID_UNTIL": ("valid_until", "срок действия", "Срок действия"),
    "DIRECTOR_NAME": ("director_name", "Подписант", "генеральный директор"),
    "PRICE_TOTAL": ("price_total", "Стоимость", "стоимость"),
    "OUTGOING_NUMBER": ("outgoing_number", "исходящий номер", "CONTRACT_NUMBER"),
    "WORK_TITLE": ("WORK_TITLE_1", "WORK_TITLE_NOMINATIVE", "Вид_работ", "Вид работ", "вид работ"),
    "Вид_работ": ("WORK_TITLE", "WORK_TITLE_1", "WORK_TITLE_NOMINATIVE", "Вид работ"),
}


def _norm_token(value: str) -> str:
    return re.sub(r"[^0-9A-ZА-ЯЁ]+", "", str(value or "").upper())


def _mapping_suggestions(
    placeholders: list[str],
    headers: list[str],
    *,
    column_samples: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    normalized_headers = {_norm_token(header): header for header in headers}
    result: list[dict[str, Any]] = []
    for placeholder in placeholders:
        normalized_placeholder = _norm_token(placeholder)
        candidates: list[dict[str, Any]] = []
        if normalized_placeholder in normalized_headers:
            candidates.append({"column": normalized_headers[normalized_placeholder], "confidence": 1.0, "reason": "exact_placeholder_match"})
        for alias in FIELD_ALIASES.get(placeholder, ()):
            normalized_alias = _norm_token(alias)
            if normalized_alias in normalized_headers:
                candidates.append({"column": normalized_headers[normalized_alias], "confidence": 0.85, "reason": "known_alias"})
        if not candidates:
            for header in headers:
                header_norm = _norm_token(header)
                if not header_norm:
                    continue
                if normalized_placeholder and (normalized_placeholder in header_norm or header_norm in normalized_placeholder):
                    candidates.append({"column": header, "confidence": 0.55, "reason": "partial_name_match"})
        if not candidates:
            from src.campaigns.placeholder_semantic import semantic_match_recipient_column

            semantic = semantic_match_recipient_column(
                placeholder,
                headers,
                samples=column_samples,
            )
            if semantic is not None:
                candidates.append(
                    {
                        "column": semantic.canonical,
                        "confidence": min(float(semantic.score), 0.84),
                        "reason": "semantic_match",
                    }
                )
        unique_candidates: list[dict[str, Any]] = []
        seen = set()
        for item in sorted(candidates, key=lambda candidate: float(candidate.get("confidence") or 0), reverse=True):
            column = str(item.get("column") or "")
            if column in seen:
                continue
            seen.add(column)
            unique_candidates.append(item)
        result.append({
            "placeholder": placeholder,
            "status": "mapped" if unique_candidates else "needs_review",
            "candidates": unique_candidates[:5],
        })
    return result


def _normalization_plan(templates: list[dict[str, Any]]) -> dict[str, Any]:
    kp_template = next((template for template in templates if template.get("kind") == "kp" and template.get("exists")), None)
    profile = kp_template.get("style_profile") if isinstance(kp_template, dict) else {}
    risks = list(profile.get("layout_risks") or []) if isinstance(profile, dict) else []
    if kp_template and kp_template.get("format") != "docx":
        risks.append("kp_template_is_not_docx")
    return {
        "applies_to": "kp",
        "renderer": "docx_template_pdf_fit",
        "style_source": kp_template.get("filename") if kp_template else "",
        "one_page_required": True,
        "image_policy": "preserve_template_layout",
        "preview_required": True,
        "mass_generation_requires_approval": True,
        "risks": sorted(set(risks)),
    }


def build_template_analysis_context(
    *,
    job_id: str | None,
    document_mode: str | None,
) -> dict[str, Any]:
    paths = resolve_job_paths(job_id)
    templates = [
        analyze_template_file(path, kind=kind)
        for kind, path in _template_candidates(paths.templates_dir, document_mode)
    ]
    placeholders = sorted(
        {
            placeholder
            for template in templates
            for placeholder in template.get("placeholders", []) or []
            if isinstance(placeholder, str)
        }
    )
    data = _sample_data_context(paths.data_xlsx)
    header_list = [str(header) for header in data.get("headers") or []]
    headers = set(header_list)
    missing_header_matches = [placeholder for placeholder in placeholders if placeholder not in headers]
    return {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id or "",
        "document_mode": document_mode,
        "templates_dir_exists": paths.templates_dir.exists(),
        "templates": templates,
        "data": data,
        "all_placeholders": placeholders,
        "placeholders_without_same_named_column": missing_header_matches,
        "field_mapping_suggestions": _mapping_suggestions(placeholders, header_list),
        "normalization_plan": _normalization_plan(templates),
        "guidance": (
            "Use this as read-only template context. Propose a mapping/profile first; "
            "do not claim that files were edited until a deterministic tool applies changes."
        ),
    }


def save_template_analysis_context(context: dict[str, Any], *, job_id: str | None) -> dict[str, Any]:
    from src.jobs.state import save_agent_state

    return save_agent_state("template_analysis", context, job_id)