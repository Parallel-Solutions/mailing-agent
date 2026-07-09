from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader


def count_pdf_pages(path: Path | str) -> int | None:
    pdf_path = Path(path)
    if not pdf_path.exists():
        return None
    try:
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception:
        return None


def validate_kp_pdf(path: Path | str, *, expected_pages: int = 1) -> dict[str, Any]:
    pdf_path = Path(path)
    if not pdf_path.exists():
        return {
            "ok": False,
            "reason": "missing",
            "message": "PDF не создан.",
            "page_count": None,
        }
    try:
        if pdf_path.stat().st_size <= 4:
            return {
                "ok": False,
                "reason": "empty",
                "message": "PDF пустой.",
                "page_count": None,
            }
        with pdf_path.open("rb") as handle:
            if handle.read(4) != b"%PDF":
                return {
                    "ok": False,
                    "reason": "not_pdf",
                    "message": "Файл не является PDF.",
                    "page_count": None,
                }
    except OSError as exc:
        return {
            "ok": False,
            "reason": "io_error",
            "message": f"Не удалось прочитать PDF: {exc}",
            "page_count": None,
        }

    page_count = count_pdf_pages(pdf_path)
    if page_count != expected_pages:
        if page_count is None:
            message = "Не удалось определить количество страниц PDF."
        else:
            message = f"КП должно быть на {expected_pages} странице, сейчас страниц: {page_count}."
        return {
            "ok": False,
            "reason": "page_count",
            "message": message,
            "page_count": page_count,
        }

    return {
        "ok": True,
        "reason": "",
        "message": "",
        "page_count": page_count,
    }