"""Inline computed CSS so imported HTML keeps appearance in GrapesJS."""

from __future__ import annotations

import json
from typing import Iterable

try:
    from src.utils.logger import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)


_INLINE_STYLE_PROPS: tuple[str, ...] = (
    "color",
    "background-color",
    "background",
    "background-image",
    "background-size",
    "background-position",
    "background-repeat",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "text-align",
    "line-height",
    "letter-spacing",
    "text-decoration",
    "text-transform",
    "text-indent",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "border-top",
    "border-right",
    "border-bottom",
    "border-left",
    "border-radius",
    "border-collapse",
    "border-spacing",
    "table-layout",
    "width",
    "max-width",
    "min-width",
    "height",
    "min-height",
    "max-height",
    "vertical-align",
    "display",
    "list-style-type",
    "white-space",
    "float",
    "opacity",
    "box-shadow",
    "object-fit",
    "object-position",
    "position",
    "top",
    "right",
    "bottom",
    "left",
    "z-index",
    "overflow",
    "overflow-x",
    "overflow-y",
    "column-count",
    "column-gap",
    "gap",
    "row-gap",
    "align-items",
    "justify-content",
    "flex-direction",
    "flex-wrap",
)

_SKIP_VALUES = frozenset(
    {
        "",
        "none",
        "auto",
        "normal",
        "0px",
        "rgba(0, 0, 0, 0)",
        "transparent",
        "initial",
        "inherit",
        "static",
        "visible",
        "stretch",
        "fill",
        "1",
        "0",
    }
)


def _build_inline_script(props: Iterable[str]) -> str:
    props_json = json.dumps(list(props))
    return f"""() => {{
  const root = document.getElementById('ma-inline-root');
  if (!root) return '';
  const props = {props_json};
  const skipValues = new Set({json.dumps(sorted(_SKIP_VALUES))});
  root.querySelectorAll('*').forEach((el) => {{
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') return;
    const computed = getComputedStyle(el);
    const existing = el.getAttribute('style') || '';
    const parts = [];
    for (const prop of props) {{
      const value = computed.getPropertyValue(prop);
      if (!value || skipValues.has(value.trim())) continue;
      parts.push(`${{prop}}:${{value}}`);
    }}
    if (parts.length) {{
      el.setAttribute('style', existing ? `${{existing}};${{parts.join(';')}}` : parts.join(';'));
    }}
  }});
  document.querySelectorAll('style').forEach((node) => node.remove());
  return root.innerHTML;
}}"""


def _build_document_html(fragment: str, *, head_styles: str = "", viewport_width: int = 640) -> str:
    extra_head = head_styles or ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<style>html,body{{margin:0;padding:0;background:#fff;}}"
        f"#ma-inline-root{{width:{viewport_width}px;max-width:100%;margin:0 auto;}}</style>"
        f"{extra_head}</head><body><div id=\"ma-inline-root\">{fragment}</div></body></html>"
    )


def inline_html_styles(
    html: str,
    *,
    head_styles: str = "",
    viewport_width: int = 640,
) -> str | None:
    fragment = (html or "").strip()
    if not fragment:
        return None

    width = max(320, min(1200, int(viewport_width or 640)))

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        logger.warning("html_style_inliner_playwright_unavailable", error=str(exc))
        return fragment

    document_html = _build_document_html(fragment, head_styles=head_styles, viewport_width=width)
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(
                viewport={"width": width, "height": 900},
                device_scale_factor=1,
            )
            page.set_content(document_html, wait_until="networkidle", timeout=60_000)
            inlined = page.evaluate(_build_inline_script(_INLINE_STYLE_PROPS))
            browser.close()
            browser = None
        if not isinstance(inlined, str) or not inlined.strip():
            return fragment
        return inlined.strip()
    except Exception as exc:
        logger.warning("html_style_inliner_failed", error=str(exc))
        return fragment
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
