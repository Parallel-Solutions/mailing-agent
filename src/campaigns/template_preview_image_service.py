"""Generate cached PNG preview images for template library tiles."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.campaigns import template_service, template_starters
from src.campaigns.chain_template_utils import substitute_chain_buttons_preview
from src.generator.generation.pdf_preview_image import render_pdf_first_page_to_png
from src.infra.object_store import ObjectNotFoundError, get_bytes, put_bytes

_PREVIEW_SAMPLE = {
    "company": "ООО Пример",
    "contact_name": "Иван Иванов",
    "email": "ivan@example.com",
    "region": "Москва",
    "campaign_name": "Тестовая рассылка",
}

_EMAIL_BG = "#f4f6f5"
_EMAIL_BG_CARD = "#ffffff"
_EMAIL_MAX_WIDTH = "600px"

_VIEWPORT_WIDTH = 640
_VIEWPORT_HEIGHT = 480


def _template_preview_storage_key(template_id: str, version_id: str) -> str:
    return f"template-library/{template_id}/{version_id}/preview.png"


def _starter_preview_storage_key(starter_id: str) -> str:
    return f"template-starters/{starter_id}/preview.png"


def build_email_preview_document(html: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
        f"<body style=\"margin:0;padding:32px 16px;background:{_EMAIL_BG}\">"
        f"<div style=\"max-width:{_EMAIL_MAX_WIDTH};margin:0 auto;background:{_EMAIL_BG_CARD};"
        "border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.08);overflow:hidden\">"
        f"{html}"
        "</div></body></html>"
    )


def substitute_preview_sample_values(text: str) -> str:
    result = text or ""
    for key, value in _PREVIEW_SAMPLE.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def _render_html_to_png_sync(html: str) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                device_scale_factor=1,
            )
            page.set_content(html, wait_until="networkidle", timeout=60_000)
            png = page.screenshot(type="png", full_page=False)
            browser.close()
            browser = None
            return png
    except Exception:
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def render_html_to_png(html: str) -> bytes | None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _render_html_to_png_sync(html)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_render_html_to_png_sync, html)
        return future.result()


def _render_email_html_preview_png(body_html: str) -> bytes | None:
    html = substitute_preview_sample_values(body_html)
    html = substitute_chain_buttons_preview(html)
    document = build_email_preview_document(html)
    return render_html_to_png(document)


def _render_document_bytes_preview_png(data: bytes, filename: str) -> bytes | None:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return render_pdf_first_page_to_png(data)

    with TemporaryDirectory(prefix="template-preview-image-") as temp_dir:
        root = Path(temp_dir)
        preview_path = root / "preview.pdf"
        if suffix == ".docx":
            source_path = root / filename
            source_path.write_bytes(data)
            from src.generator.generation.template_preview import _convert_preview_docx_to_pdf

            converted = _convert_preview_docx_to_pdf(source_path, root / "converted")
        elif suffix in {".html", ".htm"}:
            from src.generator.generation.pdf_converter import convert_html_to_pdf

            converted = convert_html_to_pdf(
                template_service._decode_text(data),
                preview_path,
                filename=filename,
            )
        else:
            converted = None
        if converted is None or not converted.exists():
            return None
        return render_pdf_first_page_to_png(converted.read_bytes())


def _generate_template_preview_png(template: dict[str, Any], owner_username: str) -> bytes | None:
    template_type = str(template.get("template_type") or "")
    version = template.get("version") or {}
    template_id = str(template.get("id") or "")

    if template_type == "email":
        body_html = str(version.get("body_html") or "")
        if not body_html.strip():
            return None
        return _render_email_html_preview_png(body_html)

    if not template_service._is_file_document_template(template_type):
        return None

    filename = str(version.get("filename") or "")
    if not filename:
        return None

    try:
        preview_file = template_service.build_file_preview(template_id, owner_username)
    except RuntimeError:
        return None
    if preview_file is None:
        return None
    return render_pdf_first_page_to_png(preview_file["content"])


def _generate_starter_preview_png(starter: dict[str, Any]) -> bytes | None:
    starter_id = str(starter.get("id") or "")
    template_type = str(starter.get("template_type") or "")

    if template_type == "email":
        body_html = str(starter.get("body_html") or "")
        if not body_html.strip():
            return None
        return _render_email_html_preview_png(body_html)

    docx_bytes = template_starters.get_starter_document_bytes(starter_id)
    if docx_bytes is None:
        return None
    filename = str(starter.get("filename") or "starter.docx")
    return _render_document_bytes_preview_png(docx_bytes, filename)


def _preview_image_payload(*, content: bytes, etag: str) -> dict[str, Any]:
    return {
        "content": content,
        "filename": "preview.png",
        "media_type": "image/png",
        "etag": etag,
    }


def get_template_preview_image(template_id: str, owner_username: str) -> dict[str, Any] | None:
    template = template_service.get_template(template_id, owner_username)
    if template is None:
        return None

    version = template.get("version") or {}
    version_id = str(version.get("id") or "")
    if not version_id:
        return None

    storage_key = _template_preview_storage_key(template_id, version_id)
    try:
        content = get_bytes(storage_key)
        return _preview_image_payload(content=content, etag=version_id)
    except ObjectNotFoundError:
        pass

    png = _generate_template_preview_png(template, owner_username)
    if png is None:
        return None

    put_bytes(storage_key, png, content_type="image/png")
    return _preview_image_payload(content=png, etag=version_id)


def get_starter_preview_image(starter_id: str) -> dict[str, Any] | None:
    starter = template_starters.get_starter(starter_id)
    if starter is None:
        return None

    storage_key = _starter_preview_storage_key(starter_id)
    try:
        content = get_bytes(storage_key)
        return _preview_image_payload(content=content, etag=starter_id)
    except ObjectNotFoundError:
        pass

    png = _generate_starter_preview_png(starter)
    if png is None:
        return None

    put_bytes(storage_key, png, content_type="image/png")
    return _preview_image_payload(content=png, etag=starter_id)
