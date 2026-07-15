from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import CertificationResult, TemplatePackage
from .protocol import PLACEHOLDER_LIKE_RE
from .renderer import render_package_to_pdf
from .store import AdaptiveTemplateStore
class _MalformedCMapNoiseFilter(logging.Filter):
    _PREFIXES = ("Skipping broken line", "Got invalid hex string")

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.getMessage()).startswith(self._PREFIXES)


_PYPDF_CMAP_LOGGER = logging.getLogger("pypdf._cmap")
if not any(isinstance(item, _MalformedCMapNoiseFilter) for item in _PYPDF_CMAP_LOGGER.filters):
    _PYPDF_CMAP_LOGGER.addFilter(_MalformedCMapNoiseFilter())


_STANDARD_VALUES = {
    "DATE": "13.07.2026",
    "OUTGOING_NUMBER": "101",
    "ADM_NAME": "Администрация муниципального образования",
    "ADM_NAME_1": "Администрации муниципального образования",
    "HEAD_FIO": "Иванов Иван Иванович",
    "HEAD_FIO_SHORT": "И. И. Иванов",
    "HEAD_GREETING": "Уважаемый Иван Иванович!",
    "MUN_NAME": "городской округ Примерный",
    "MUN_NAME_1": "городского округа Примерного",
    "MUN_NAME_2": "городскому округу Примерному",
    "MUN_R_NAME": "Примерный муниципальный район",
    "MUN_R_NAME_1": "Примерного муниципального района",
    "SUB_RF": "Московская область",
    "SUB_RF_1": "Московской области",
    "WORK_TITLE": "разработка документов территориального планирования",
    "WORK_TITLE_1": "разработке документов территориального планирования",
    "WORK_SHORT_NAME": "документы территориального планирования",
    "WORK_TYPE_LABEL": "территориальное планирование",
    "WORK_RESULT_NAME": "проект документа",
    "WORK_SCOPE_FRAGMENT": "Примерного муниципального района Московской области",
    "MUN_R_SCOPE_FRAGMENT": "Примерного муниципального района Московской области",
    "EMAIL_OSN": "example@example.ru",
    "TEL_OSN": "+7 999 000-00-00",
    "ADRES": "г. Москва, ул. Примерная, д. 1",
}


_FIXED_LENGTH_FIELDS = {"DATE", "OUTGOING_NUMBER", "CONTRACT_NUMBER", "EMAIL_OSN", "TEL_OSN"}

def certification_context(fields: tuple[str, ...], profile: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        normal = _STANDARD_VALUES.get(field, f"Тестовое значение {field}")
        if profile == "short":
            result[field] = normal[: max(4, min(len(normal), 12))]
        elif profile == "stress" and field not in _FIXED_LENGTH_FIELDS:
            result[field] = normal if len(normal) >= 55 else f"{normal} — расширенное проверочное значение максимальной ожидаемой длины"
        else:
            result[field] = normal
    return result


_VALIDITY_TEXT_RE = re.compile(
    r"Срок\s+действия\s+коммерческого\s+предложения.*?\d{2}\.\d{2}\.\d{4}",
    re.IGNORECASE | re.DOTALL,
)


def _pdf_layout_issues(pdf_path: Path) -> list[dict[str, Any]]:
    try:
        import fitz
    except ImportError:
        return [{"type": "layout_check_unavailable", "message": "PyMuPDF is unavailable"}]
    issues: list[dict[str, Any]] = []
    try:
        with fitz.open(pdf_path) as document:
            for page_index, page in enumerate(document, start=1):
                validity_boxes = [
                    fitz.Rect(block[:4])
                    for block in page.get_text("blocks")
                    if _VALIDITY_TEXT_RE.search(str(block[4] or ""))
                ]
                if not validity_boxes:
                    continue
                for image in page.get_image_info(xrefs=True):
                    if int(image.get("width") or 0) <= 2 or int(image.get("height") or 0) <= 2:
                        continue
                    image_box = fitz.Rect(image.get("bbox"))
                    if image_box.get_area() < page.rect.get_area() * 0.015:
                        continue
                    for text_box in validity_boxes:
                        intersection = image_box & text_box
                        if intersection.is_empty or intersection.get_area() < 1:
                            continue
                        issues.append(
                            {
                                "type": "image_overlaps_validity_date",
                                "page": page_index,
                                "text_box": list(text_box),
                                "image_box": list(image_box),
                            }
                        )
    except Exception as exc:
        return [{"type": "layout_check_failed", "message": f"{type(exc).__name__}: {exc}"}]
    return issues

# The active checker uses the permissively licensed PDFium engine.
from .pdf_layout_check import pdf_layout_issues as _pdf_layout_issues

def certify_template(store: AdaptiveTemplateStore, package: TemplatePackage, *, activate: bool = False) -> CertificationResult:
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    checks: list[dict[str, Any]] = [
        {"name": "explicit_fields", "status": "passed", "count": len(package.fields)},
        {"name": "format_adapter", "status": "passed", "adapter": package.adapter},
    ]
    artifacts: list[str] = []
    error = ""
    status = "passed"
    artifact_dir = store.version_dir(package.template_id) / "certification"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        for profile in ("short", "normal", "stress"):
            pdf_path = artifact_dir / f"{profile}.pdf"
            profile_context = certification_context(package.fields, profile)
            render_package_to_pdf(
                package,
                store.source_path(package.template_id),
                profile_context,
                pdf_path,
            )
            reader = PdfReader(str(pdf_path))
            page_count = len(reader.pages)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            unresolved = bool(PLACEHOLDER_LIKE_RE.search(text))
            normalized_text = " ".join(text.split()).casefold()
            missing_values = [
                field
                for field, value in profile_context.items()
                if " ".join(str(value).split()).casefold() not in normalized_text
            ]
            layout_issues = _pdf_layout_issues(pdf_path)
            passed = page_count == 1 and not unresolved and not missing_values and not layout_issues
            checks.append(
                {
                    "name": f"render_{profile}",
                    "status": "passed" if passed else "failed",
                    "page_count": page_count,
                    "unresolved_placeholders": unresolved,
                    "missing_rendered_values": missing_values,
                    "layout_issues": layout_issues,
                }
            )
            artifacts.append(str(pdf_path.relative_to(store.root)).replace("\\", "/"))
            if not passed:
                status = "failed"
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        checks.append({"name": "render_pipeline", "status": "failed", "error": error})

    result = CertificationResult(
        template_id=package.template_id,
        status=status,
        created_at=created_at,
        checks=tuple(checks),
        artifacts=tuple(artifacts),
        error=error,
    )
    store.save_certification(result)
    if activate and result.passed and store.latest_template_id() == package.template_id:
        store.activate(package.template_id)
    return result
