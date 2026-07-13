from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .base import TemplateRenderError
from .models import TemplatePackage
from .registry import TemplateAdapterRegistry
from .store import AdaptiveTemplateStore


def _adapter_for(package: TemplatePackage):
    registry = TemplateAdapterRegistry()
    for adapter in registry.adapters:
        if adapter.name == package.adapter:
            return adapter
    raise TemplateRenderError(f"Template adapter {package.adapter} is unavailable")


def _is_one_page(pdf_path: Path) -> bool:
    try:
        return len(PdfReader(str(pdf_path)).pages) == 1
    except Exception:
        return False


def render_package_to_pdf(
    package: TemplatePackage,
    source_path: Path,
    context: dict[str, Any],
    output_path: Path,
) -> Path:
    adapter = _adapter_for(package)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if package.source_format == "pdf":
        rendered = adapter.render(source_path, context, output_path)
        if not _is_one_page(rendered):
            rendered.unlink(missing_ok=True)
            raise TemplateRenderError("Commercial proposal PDF must contain exactly one page")
        return rendered

    scales = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7)
    with tempfile.TemporaryDirectory(prefix="adaptive-render-") as temp_dir:
        temp_root = Path(temp_dir)
        if package.source_format == "docx":
            from src.generator.generation.docx_preview_normalizer import normalize_docx_for_preview
            from src.generator.generation.pdf_converter import convert_docx_batch

            for index, scale in enumerate(scales):
                scaled_context = {**context, "__ADAPTIVE_FONT_SCALE__": scale}
                docx_path = adapter.render(source_path, scaled_context, temp_root / f"rendered-{index}.docx")
                converted = convert_docx_batch([docx_path], temp_root).get(docx_path)
                if converted is not None and converted.exists() and _is_one_page(converted):
                    output_path.write_bytes(converted.read_bytes())
                    return output_path

                compact_path = temp_root / f"compact-{index}.docx"
                normalize_docx_for_preview(
                    docx_path,
                    compact_path,
                    compact_body=True,
                    max_body_font_half_points=20,
                )
                converted = convert_docx_batch([compact_path], temp_root).get(compact_path)
                if converted is not None and converted.exists() and _is_one_page(converted):
                    output_path.write_bytes(converted.read_bytes())
                    return output_path
            raise TemplateRenderError("DOCX template does not fit on one page at the minimum field font size")
        if package.source_format in {"html", "htm"}:
            from src.generator.generation.pdf_converter import convert_html_to_pdf

            for index, scale in enumerate(scales):
                scaled_context = {**context, "__ADAPTIVE_PAGE_SCALE__": scale}
                html_path = adapter.render(source_path, scaled_context, temp_root / f"index-{index}.html")
                candidate = temp_root / f"rendered-{index}.pdf"
                converted = convert_html_to_pdf(html_path.read_text(encoding="utf-8"), candidate)
                if converted is None or not converted.exists():
                    continue
                if _is_one_page(converted):
                    output_path.write_bytes(converted.read_bytes())
                    return output_path
            raise TemplateRenderError("HTML template does not fit on one page at the minimum page scale")
    raise TemplateRenderError(f"No PDF rendering pipeline for {package.source_format}")

def render_active_template(
    templates_dir: Path,
    context: dict[str, Any],
    output_path: Path,
    *,
    kind: str = "kp",
) -> Path:
    store = AdaptiveTemplateStore(templates_dir, kind)
    package = store.load_active()
    if package is None:
        raise TemplateRenderError(f"No active adaptive {kind} template")
    certification = store.load_certification(package.template_id)
    if certification.get("status") != "passed":
        raise TemplateRenderError("The active template is not certified")
    return render_package_to_pdf(package, store.source_path(package.template_id), context, output_path)
