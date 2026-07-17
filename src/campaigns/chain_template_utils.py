"""Placeholder helpers for chain branch buttons inside email templates."""

from __future__ import annotations

import re
from html import escape

from src.utils.config import settings

CHAIN_BUTTONS_MARKER = 'data-ma-chain-buttons="1"'
TEXT_CHAIN_BUTTONS_MARKER = "[CHAIN_BUTTONS]"

_PLACEHOLDER_RE = re.compile(
    r'<(?P<tag>div|td)\b(?P<attrs>[^>]*\bdata-ma-chain-buttons\s*=\s*["\']1["\'][^>]*)>.*?</(?P=tag)>',
    re.IGNORECASE | re.DOTALL,
)

_DEFAULT_WRAPPER_STYLE = "text-align:center;padding:8px 0"
_PREVIEW_LABELS = ("Вариант 1", "Вариант 2")
_BUTTON_STYLE = (
    "display:inline-block;padding:8px 16px;background:#1677ff;color:#fff;"
    "text-decoration:none;border-radius:4px"
)
_PREVIEW_BUTTON_STYLE = (
    "display:inline-block;padding:8px 16px;background:#d9d9d9;color:#595959;"
    "border-radius:4px"
)


def has_chain_button_placeholder(html: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(html or ""))


def _public_base_url() -> str:
    return str(getattr(settings, "public_base_url", "") or "http://localhost:9806").rstrip("/")


def _extract_wrapper_style(attrs: str) -> str:
    style_match = re.search(r'style\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE)
    if not style_match:
        return _DEFAULT_WRAPPER_STYLE
    preserved: list[str] = []
    for part in style_match.group(1).split(";"):
        token = part.strip()
        if token.startswith("text-align:") or token.startswith("padding"):
            preserved.append(token)
    return ";".join(preserved) if preserved else _DEFAULT_WRAPPER_STYLE


def build_chain_buttons_html(
    buttons: list[tuple[str, str]],
    *,
    wrapper_style: str = _DEFAULT_WRAPPER_STYLE,
) -> tuple[str, str]:
    if not buttons:
        return "", ""
    base = _public_base_url()
    html_parts = [f'<div style="{wrapper_style}">']
    text_parts: list[str] = []
    for label, token in buttons:
        link = f"{base}/chain/branch/{token}"
        safe_label = escape(label)
        html_parts.append(
            f'<p style="margin:0 0 8px"><a href="{link}" style="{_BUTTON_STYLE}">{safe_label}</a></p>'
        )
        text_parts.append(f"{label}: {link}")
    html_parts.append("</div>")
    return "".join(html_parts), "\n".join(text_parts)


def build_chain_buttons_preview_html(*, wrapper_style: str = _DEFAULT_WRAPPER_STYLE) -> str:
    parts = [f'<div style="{wrapper_style}">']
    for label in _PREVIEW_LABELS:
        parts.append(
            f'<p style="margin:0 0 8px"><span style="{_PREVIEW_BUTTON_STYLE}">{label}</span></p>'
        )
    parts.append("</div>")
    return "".join(parts)


def inject_chain_buttons(
    html: str,
    text: str,
    buttons: list[tuple[str, str]],
) -> tuple[str, str]:
    if not buttons:
        return html, text

    match = _PLACEHOLDER_RE.search(html or "")
    if match:
        wrapper_style = _extract_wrapper_style(match.group("attrs"))
        buttons_html, buttons_text = build_chain_buttons_html(buttons, wrapper_style=wrapper_style)
        html = (html or "")[: match.start()] + buttons_html + (html or "")[match.end() :]
        if TEXT_CHAIN_BUTTONS_MARKER in (text or ""):
            text = (text or "").replace(TEXT_CHAIN_BUTTONS_MARKER, buttons_text)
        else:
            text = (text or "") + "\n\n" + buttons_text
        return html, text

    buttons_html, buttons_text = build_chain_buttons_html(buttons, wrapper_style="margin-top:16px")
    return (html or "") + buttons_html, (text or "") + "\n\n" + buttons_text


def strip_chain_button_placeholder(html: str) -> str:
    return _PLACEHOLDER_RE.sub("", html or "")


def substitute_chain_buttons_preview(html: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        wrapper_style = _extract_wrapper_style(match.group("attrs"))
        return build_chain_buttons_preview_html(wrapper_style=wrapper_style)

    return _PLACEHOLDER_RE.sub(_replace, html or "")
