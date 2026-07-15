from __future__ import annotations

from pathlib import Path

from .base import TemplateAdapter, TemplateCompileError


class TemplateAdapterRegistry:
    def __init__(self, adapters: tuple[TemplateAdapter, ...] | None = None) -> None:
        self._adapters = adapters or default_adapters()

    @property
    def adapters(self) -> tuple[TemplateAdapter, ...]:
        return self._adapters

    def resolve(self, source_path: Path) -> TemplateAdapter:
        for adapter in self._adapters:
            if adapter.probe(source_path):
                return adapter
        supported = sorted({suffix for adapter in self._adapters for suffix in adapter.formats})
        raise TemplateCompileError(
            f"Unsupported or malformed template {source_path.name}. Supported formats: {', '.join(supported)}"
        )


def default_adapters() -> tuple[TemplateAdapter, ...]:
    from .adapters.docx import DocxTemplateAdapter
    from .adapters.html import HtmlTemplateAdapter
    from .adapters.pdf import PdfAcroFormAdapter, PdfTextPlaceholderAdapter

    return (DocxTemplateAdapter(), HtmlTemplateAdapter(), PdfAcroFormAdapter(), PdfTextPlaceholderAdapter())
