from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from src.generator.generation.config_generator import GOTENBERG_CONVERT_TIMEOUT_SECONDS

try:
    from src.utils.logger import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)


VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "docxjs"
JSZIP_PATH = VENDOR_DIR / "jszip.min.js"
DOCX_PREVIEW_PATH = VENDOR_DIR / "docx-preview.min.js"


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


def convert_docx_to_pdf_with_docxjs(docx_path: Path, output_path: Path) -> Optional[Path]:
    if not docxjs_assets_available():
        logger.warning("docxjs_assets_missing", vendor_dir=str(VENDOR_DIR))
        return None
    if not docx_path.exists() or docx_path.suffix.lower() != ".docx":
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        logger.warning("docxjs_playwright_unavailable", error=str(exc))
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    docx_b64 = base64.b64encode(docx_path.read_bytes()).decode("ascii")
    timeout_ms = max(1, GOTENBERG_CONVERT_TIMEOUT_SECONDS) * 1000
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1)
            page.set_content(_load_docxjs_page_html(), wait_until="domcontentloaded", timeout=timeout_ms)
            page.evaluate(
                """async ({ docxBase64 }) => {
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
                }""",
                {"docxBase64": docx_b64},
            )
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
        output_path.write_bytes(pdf_bytes)
        return output_path if output_path.exists() else None
    except Exception as exc:
        logger.warning("docxjs_convert_failed", docx_path=str(docx_path), error=str(exc))
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
