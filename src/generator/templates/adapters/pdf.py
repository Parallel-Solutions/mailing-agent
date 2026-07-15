from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from ..base import TemplateAdapter, TemplateCompileError, TemplateRenderError
from ..models import TemplateOccurrence
from ..protocol import FIELD_NAME_PATTERN, PLACEHOLDER_RE, require_context


_FIELD_RE = re.compile(r"^" + FIELD_NAME_PATTERN + r"$")
_FIELD_INSTANCE_RE = re.compile(r"^(?P<field>" + FIELD_NAME_PATTERN + r")__(?P<instance>[1-9][0-9]*)$")


def _canonical_form_field_name(raw_name: str) -> str | None:
    token = str(raw_name).strip()
    if token.startswith("{{") and token.endswith("}}"):
        token = token[2:-2].strip()
    instance_match = _FIELD_INSTANCE_RE.fullmatch(token)
    if instance_match:
        return instance_match.group("field")
    return token if _FIELD_RE.fullmatch(token) else None


def _form_field_mapping(source_path: Path) -> dict[str, str]:
    fields = PdfReader(str(source_path)).get_fields() or {}
    mapping: dict[str, str] = {}
    for raw_name in fields:
        field_name = _canonical_form_field_name(str(raw_name))
        if field_name:
            mapping[str(raw_name)] = field_name
    return mapping


class PdfAcroFormAdapter(TemplateAdapter):
    name = "pdf-acroform-v1"
    formats = (".pdf",)

    def probe(self, source_path: Path) -> bool:
        if source_path.suffix.lower() != ".pdf" or not source_path.is_file():
            return False
        try:
            return bool(_form_field_mapping(source_path))
        except Exception:
            return False

    def inspect(self, source_path: Path) -> tuple[tuple[TemplateOccurrence, ...], dict[str, Any], tuple[str, ...]]:
        mapping = _form_field_mapping(source_path)
        if not mapping:
            raise TemplateCompileError("PDF has no named AcroForm fields matching UPPER_SNAKE_CASE")
        occurrences = tuple(TemplateOccurrence(field, f"acroform:{raw}") for raw, field in mapping.items())
        return occurrences, {"output": "pdf", "flattened": True, "adaptive_fit": "field-appearance"}, ()

    def render(self, source_path: Path, context: dict[str, Any], output_path: Path) -> Path:
        mapping = _form_field_mapping(source_path)
        values = require_context(tuple(mapping.values()), context)
        reader = PdfReader(str(source_path))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        font_id = None
        try:
            acro_form = reader.trailer["/Root"]["/AcroForm"].get_object()
            default_resources = acro_form.get("/DR", {}).get_object()
            fonts = default_resources.get("/Font", {}).get_object()
            font_id = str(next(iter(fonts.keys()))) if fonts else None
        except Exception:
            font_id = None
        raw_values = {
            raw: (values[field], font_id, 0) if font_id else values[field]
            for raw, field in mapping.items()
        }
        try:
            writer.update_page_form_field_values(list(writer.pages), raw_values, auto_regenerate=False, flatten=True)
        except Exception as exc:
            raise TemplateRenderError(f"Could not fill and flatten PDF form: {exc}") from exc
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as handle:
            writer.write(handle)
        return output_path


def _pdf_overlay_font() -> tuple[str, str | None]:
    configured = str(os.getenv("ADAPTIVE_PDF_FONT_PATH") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return "adaptive-unicode", str(candidate)
    return "helv", None

class PdfTextPlaceholderAdapter(TemplateAdapter):
    name = "pdf-text-overlay-v1"
    formats = (".pdf",)

    @staticmethod
    def _fitz():
        try:
            import fitz
        except ImportError:
            return None
        return fitz

    def probe(self, source_path: Path) -> bool:
        if source_path.suffix.lower() != ".pdf" or not source_path.is_file():
            return False
        try:
            if _form_field_mapping(source_path):
                return False
        except Exception:
            return False
        fitz = self._fitz()
        if fitz is None:
            return False
        try:
            with fitz.open(source_path) as document:
                return any(PLACEHOLDER_RE.search(page.get_text("text")) for page in document)
        except Exception:
            return False
    def inspect(self, source_path: Path) -> tuple[tuple[TemplateOccurrence, ...], dict[str, Any], tuple[str, ...]]:
        fitz = self._fitz()
        if fitz is None:
            raise TemplateCompileError("Text-placeholder PDF templates require PyMuPDF in the worker image")
        occurrences: list[TemplateOccurrence] = []
        with fitz.open(source_path) as document:
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text")
                for match in PLACEHOLDER_RE.finditer(text):
                    token = match.group(0)
                    for box in page.search_for(token):
                        occurrences.append(
                            TemplateOccurrence(match.group("name"), f"page:{page_index}", page=page_index, box=tuple(box))
                        )
        if not occurrences:
            raise TemplateCompileError("PDF has no searchable {{FIELD}} text placeholders")
        warning = "Literal PDF placeholders have fixed boxes; AcroForm fields are recommended for long variable text."
        return tuple(occurrences), {"output": "pdf", "flattened": True, "adaptive_fit": "fixed-box"}, (warning,)

    def render(self, source_path: Path, context: dict[str, Any], output_path: Path) -> Path:
        fitz = self._fitz()
        if fitz is None:
            raise TemplateRenderError("PyMuPDF is unavailable")
        occurrences, _, _ = self.inspect(source_path)
        values = require_context(tuple(item.field_name for item in occurrences), context)
        font_name, font_file = _pdf_overlay_font()
        with fitz.open(source_path) as document:
            for occurrence in occurrences:
                page = document[occurrence.page - 1]
                box = fitz.Rect(occurrence.box)
                page.add_redact_annot(box, fill=(1, 1, 1))
            for page in document:
                page.apply_redactions()
            for occurrence in occurrences:
                page = document[occurrence.page - 1]
                box = fitz.Rect(occurrence.box)
                value = values[occurrence.field_name]
                inserted = False
                size = max(6.0, min(12.0, box.height * 0.8))
                while size >= 6.0:
                    if page.insert_textbox(
                        box,
                        value,
                        fontsize=size,
                        fontname=font_name,
                        fontfile=font_file,
                        color=(0, 0, 0),
                    ) >= 0:
                        inserted = True
                        break
                    size -= 0.2
                if not inserted:
                    raise TemplateRenderError(
                        f"Value for {occurrence.field_name} does not fit its PDF box at the minimum font size"
                    )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            document.save(output_path, garbage=4, deflate=True)
        return output_path
