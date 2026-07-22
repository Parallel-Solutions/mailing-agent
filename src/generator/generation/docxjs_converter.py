from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from src.generator.generation.config_generator import GOTENBERG_CONVERT_TIMEOUT_SECONDS
from src.generator.generation.html_style_inliner import inline_html_styles

try:
    from src.utils.logger import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)


VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "docxjs"
JSZIP_PATH = VENDOR_DIR / "jszip.min.js"
DOCX_PREVIEW_PATH = VENDOR_DIR / "docx-preview.min.js"
_DEFAULT_DOCX_WIDTH = 794


@dataclass(frozen=True)
class DocxJsHtmlResult:
    html: str
    content_width: int = _DEFAULT_DOCX_WIDTH


def docxjs_assets_available() -> bool:
    return JSZIP_PATH.exists() and DOCX_PREVIEW_PATH.exists()


def _load_docxjs_page_html() -> str:
    jszip = JSZIP_PATH.read_text(encoding="utf-8")
    docx_preview = DOCX_PREVIEW_PATH.read_text(encoding="utf-8")
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{ margin: 0; }}
  html, body {{
    margin: 0;
    padding: 0;
    background: #fff;
  }}
  body {{
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  #container {{
    width: 100%;
  }}
  .docx-wrapper {{
    background: #fff !important;
    padding: 0 !important;
  }}
  .docx-wrapper > section.docx {{
    box-shadow: none !important;
    margin: 0 auto !important;
  }}
  @media print {{
    .docx-wrapper {{
      padding: 0 !important;
    }}
    .docx-wrapper > section.docx {{
      margin: 0 !important;
      box-shadow: none !important;
    }}
  }}
</style>
<script>{jszip}</script>
<script>{docx_preview}</script>
</head>
<body>
<div id="style-container"></div>
<div id="container"></div>
</body>
</html>"""


_DOCXJS_RENDER_JS = """async ({ docxBase64 }) => {
  if (!window.docx || typeof window.docx.renderAsync !== "function") {
    throw new Error("docx-preview is not available");
  }
  const binary = atob(docxBase64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const container = document.getElementById("container");
  const styleContainer = document.getElementById("style-container");
  await window.docx.renderAsync(bytes, container, styleContainer, {
    className: "docx",
    inWrapper: true,
    hideWrapperOnPrint: false,
    ignoreWidth: false,
    ignoreHeight: false,
    ignoreFonts: false,
    breakPages: true,
    ignoreLastRenderedPageBreak: false,
    experimental: true,
    trimXmlDeclaration: true,
    useBase64URL: true,
    renderHeaders: true,
    renderFooters: true,
    renderFootnotes: true,
    renderEndnotes: true,
    renderAltChunks: true
  });
  document.body.dataset.docxjsReady = "1";
}"""

_MEASURE_WIDTH_JS = """() => {
  const section = document.querySelector('.docx-wrapper > section.docx');
  if (section) {
    const rect = section.getBoundingClientRect();
    const width = Math.round(rect.width || section.offsetWidth || 0);
    if (width > 0) return width;
  }
  const wrapper = document.querySelector('.docx-wrapper');
  if (wrapper) {
    const width = Math.round(wrapper.getBoundingClientRect().width || wrapper.offsetWidth || 0);
    if (width > 0) return width;
  }
  return 794;
}"""


def _clamp_content_width(width: int | float | None) -> int:
    try:
        value = int(round(float(width or _DEFAULT_DOCX_WIDTH)))
    except (TypeError, ValueError):
        value = _DEFAULT_DOCX_WIDTH
    return max(480, min(800, value))


def _render_docx_with_docxjs(docx_bytes: bytes) -> tuple[str, str, int] | None:
    if not docxjs_assets_available():
        logger.warning("docxjs_assets_missing", vendor_dir=str(VENDOR_DIR))
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        logger.warning("docxjs_playwright_unavailable", error=str(exc))
        return None

    docx_b64 = base64.b64encode(docx_bytes).decode("ascii")
    timeout_ms = max(1, GOTENBERG_CONVERT_TIMEOUT_SECONDS) * 1000
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1)
            page.set_content(_load_docxjs_page_html(), wait_until="domcontentloaded", timeout=timeout_ms)
            page.evaluate(_DOCXJS_RENDER_JS, {"docxBase64": docx_b64})
            page.wait_for_function("document.body.dataset.docxjsReady === '1'", timeout=timeout_ms)
            styles = page.eval_on_selector("#style-container", "el => el.innerHTML") or ""
            body = page.eval_on_selector("#container", "el => el.innerHTML") or ""
            measured = page.evaluate(_MEASURE_WIDTH_JS)
            browser.close()
            browser = None
        if not body.strip():
            raise RuntimeError("docxjs returned empty HTML")
        content_width = _clamp_content_width(measured)
        return styles, body, content_width
    except Exception as exc:
        logger.warning("docxjs_render_failed", error=str(exc))
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def convert_docx_to_html_result(docx_path: Path | bytes) -> DocxJsHtmlResult | None:
    if isinstance(docx_path, bytes):
        docx_bytes = docx_path
    else:
        if not docx_path.exists() or docx_path.suffix.lower() != ".docx":
            return None
        docx_bytes = docx_path.read_bytes()

    rendered = _render_docx_with_docxjs(docx_bytes)
    if rendered is None:
        return None
    styles, body, content_width = rendered
    head_styles = f"<style>{styles}</style>" if styles.strip() else ""
    inlined = inline_html_styles(body, head_styles=head_styles, viewport_width=content_width)
    html = inlined or body
    if "data-content-width=" not in html:
        html = (
            f'<div class="docx-import-root" data-content-width="{content_width}">'
            f"{html}</div>"
        )
    return DocxJsHtmlResult(html=html, content_width=content_width)


def convert_docx_to_html_with_docxjs(docx_path: Path | bytes) -> str | None:
    result = convert_docx_to_html_result(docx_path)
    return result.html if result else None


def _convert_docx_bytes_to_pdf(docx_bytes: bytes) -> bytes | None:
    if not docx_bytes:
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        logger.warning("docxjs_playwright_unavailable", error=str(exc))
        return None

    docx_b64 = base64.b64encode(docx_bytes).decode("ascii")
    timeout_ms = max(1, GOTENBERG_CONVERT_TIMEOUT_SECONDS) * 1000
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1)
            page.set_content(_load_docxjs_page_html(), wait_until="domcontentloaded", timeout=timeout_ms)
            page.evaluate(_DOCXJS_RENDER_JS, {"docxBase64": docx_b64})
            page.wait_for_function("document.body.dataset.docxjsReady === '1'", timeout=timeout_ms)
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            browser.close()
            browser = None
        if not pdf_bytes.startswith(b"%PDF"):
            raise RuntimeError("docxjs returned unexpected PDF payload")
        return pdf_bytes
    except Exception as exc:
        logger.warning("docxjs_convert_failed", error=str(exc))
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def convert_docx_to_pdf_bytes(docx_bytes: bytes) -> bytes | None:
    """Render DOCX to PDF bytes via docx-preview + Playwright print."""
    return _convert_docx_bytes_to_pdf(docx_bytes)


def convert_docx_to_pdf_with_docxjs(docx_path: Path | bytes, output_path: Path | None = None) -> Path | bytes | None:
    if isinstance(docx_path, bytes):
        return convert_docx_to_pdf_bytes(docx_path)

    if not docx_path.exists() or docx_path.suffix.lower() != ".docx":
        return None

    pdf_bytes = _convert_docx_bytes_to_pdf(docx_path.read_bytes())
    if pdf_bytes is None:
        return None
    if output_path is None:
        return pdf_bytes

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
    return output_path if output_path.exists() else None
