from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString

from ..base import TemplateAdapter, TemplateCompileError
from ..models import TemplateOccurrence
from ..protocol import PLACEHOLDER_RE, require_context, validate_placeholder_syntax


_BLOCKED_TAGS = ("script", "iframe", "object", "embed")


def _parse_safe_html(source_path: Path) -> BeautifulSoup:
    raw = source_path.read_text(encoding="utf-8-sig")
    lowered = raw.lower()
    if "@import" in lowered or "url(http:" in lowered or "url(https:" in lowered:
        raise TemplateCompileError("External CSS resources are not allowed in HTML templates")
    soup = BeautifulSoup(raw, "lxml")
    blocked = [tag.name for tag in soup.find_all(_BLOCKED_TAGS)]
    if blocked:
        raise TemplateCompileError("HTML template contains blocked active elements: " + ", ".join(sorted(set(blocked))))
    for tag in soup.find_all(True):
        event_attributes = [name for name in tag.attrs if str(name).lower().startswith("on")]
        if event_attributes:
            raise TemplateCompileError(f"HTML event handlers are not allowed: {', '.join(event_attributes)}")
        for attribute in ("src", "href"):
            value = str(tag.get(attribute) or "").strip()
            scheme = urlparse(value).scheme.lower()
            if scheme in {"http", "https", "javascript", "file"}:
                raise TemplateCompileError(f"External resource is not allowed in HTML template: {value}")
    return soup

class HtmlTemplateAdapter(TemplateAdapter):
    name = "html-dom-v1"
    formats = (".html", ".htm")

    def probe(self, source_path: Path) -> bool:
        return source_path.suffix.lower() in self.formats and source_path.is_file()

    def inspect(self, source_path: Path) -> tuple[tuple[TemplateOccurrence, ...], dict[str, Any], tuple[str, ...]]:
        soup = _parse_safe_html(source_path)
        occurrences: list[TemplateOccurrence] = []
        text_index = 0
        for node in soup.find_all(string=True):
            if node.parent and node.parent.name in {"style"}:
                continue
            text_index += 1
            text = str(node)
            validate_placeholder_syntax(text)
            for match in PLACEHOLDER_RE.finditer(text):
                occurrences.append(TemplateOccurrence(match.group("name"), f"text:{text_index}"))
        if not occurrences:
            raise TemplateCompileError("HTML template has no explicit {{FIELD}} placeholders")
        return tuple(occurrences), {"output": "html", "adaptive_browser_fit": True}, ()

    def render(self, source_path: Path, context: dict[str, Any], output_path: Path) -> Path:
        occurrences, _, _ = self.inspect(source_path)
        values = require_context(tuple(item.field_name for item in occurrences), context)
        page_scale = max(0.7, min(1.0, float(context.get("__ADAPTIVE_PAGE_SCALE__", 1.0))))
        soup = _parse_safe_html(source_path)
        for node in list(soup.find_all(string=True)):
            if node.parent and node.parent.name == "style":
                continue
            text = str(node)
            matches = list(PLACEHOLDER_RE.finditer(text))
            if not matches:
                continue
            cursor = 0
            replacements = []
            for match in matches:
                if match.start() > cursor:
                    replacements.append(NavigableString(text[cursor : match.start()]))
                span = soup.new_tag("span")
                span["data-adaptive-field"] = match.group("name")
                span.string = values[match.group("name")]
                replacements.append(span)
                cursor = match.end()
            if cursor < len(text):
                replacements.append(NavigableString(text[cursor:]))
            for replacement in replacements:
                node.insert_before(replacement)
            node.extract()

        head = soup.head or soup.new_tag("head")
        if soup.head is None:
            (soup.html or soup).insert(0, head)
        style = soup.new_tag("style")
        style.string = f"[data-adaptive-field]{{font:inherit;line-height:inherit;}} @media print{{body{{zoom:{page_scale:.3f};}}}}"
        head.append(style)
        fit_script = soup.new_tag("script")
        fit_script.string = """
(() => {
  const fits = (el) => {
    return el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1;
  };
  for (const box of document.querySelectorAll('[data-adaptive-container]')) {
    const initial = parseFloat(getComputedStyle(box).fontSize) || 12;
    const minimum = parseFloat(box.dataset.minFontSize) || Math.max(6, initial * 0.68);
    let low = minimum;
    let high = initial;
    box.style.fontSize = `${high}px`;
    if (!fits(box)) {
      for (let index = 0; index < 14; index += 1) {
        const middle = (low + high) / 2;
        box.style.fontSize = `${middle}px`;
        if (fits(box)) low = middle; else high = middle;
      }
      box.style.fontSize = `${low}px`;
    }
    box.dataset.adaptiveFontSize = parseFloat(box.style.fontSize).toFixed(2);
  }
  for (const el of document.querySelectorAll('[data-adaptive-field]')) {
    if (el.closest('[data-adaptive-container]')) {
      el.dataset.adaptiveFontSize = parseFloat(getComputedStyle(el).fontSize).toFixed(2);
      continue;
    }
    const initial = parseFloat(getComputedStyle(el).fontSize) || 12;
    let size = initial;
    const parent = el.parentElement;
    while (parent && (parent.scrollWidth > parent.clientWidth + 1 || parent.scrollHeight > parent.clientHeight + 1) && size > 6) {
      size -= 0.2; el.style.fontSize = `${size}px`;
    }
    el.dataset.adaptiveFontSize = size.toFixed(1);
  }
})();
"""
        (soup.body or soup).append(fit_script)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(str(soup), encoding="utf-8")
        return output_path
