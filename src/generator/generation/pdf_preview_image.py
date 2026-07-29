"""Render PDF first page to PNG bytes (PyMuPDF/fitz)."""

from __future__ import annotations

from pathlib import Path


def render_pdf_first_page_to_png(
    pdf: bytes | Path,
    *,
    scale: float = 1.6,
) -> bytes | None:
    try:
        import fitz  # type: ignore
    except Exception:
        return None

    try:
        if isinstance(pdf, Path):
            document = fitz.open(str(pdf))
        else:
            document = fitz.open(stream=pdf, filetype="pdf")
        if document.page_count < 1:
            return None
        page = document.load_page(0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")
    except Exception:
        return None


def render_pdf_first_page_to_png_data_url(pdf_path: Path, *, scale: float = 1.6) -> str:
    import base64

    payload = render_pdf_first_page_to_png(pdf_path, scale=scale)
    if not payload:
        return ""
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
