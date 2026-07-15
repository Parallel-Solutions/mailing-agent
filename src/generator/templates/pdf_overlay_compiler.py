from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import pdf_overlay as engine


_OUTGOING_NUMBER_LOOKAHEAD_RE = re.compile(
    r"(?P<number>\d{1,8})(?=\s*[-–—]?\s*\u041a\u041f)",
    re.IGNORECASE,
)


def build_pdf_overlay_html(source_path: Path, reference_context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build browser-safe HTML around the deterministic PDF overlay engine."""

    # Keep КП in the following date match so a line damaged by Word merge
    # fields can still be recognized as one outgoing-number phrase.
    engine._OUTGOING_NUMBER_RE = _OUTGOING_NUMBER_LOOKAHEAD_RE
    html, report = engine.build_pdf_overlay_html(source_path, reference_context)

    # CSS font names need quotes, but the surrounding style attribute also
    # uses double quotes. Normalize the font-family value before HTML parsing.
    html = re.sub(
        r"font-family:(?P<value>[^;]+);",
        lambda match: "font-family:" + match.group("value").replace('"', "'") + ";",
        html,
    )
    # Unitless line-height follows the adaptive font size when text shrinks.
    html = re.sub(r"line-height:[0-9.]+pt;", "line-height:1.2;", html)
    return html, report
