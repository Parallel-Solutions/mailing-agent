"""Placeholder helpers for chain branch buttons inside email templates."""

from __future__ import annotations

import re
from html import escape
from typing import TypeAlias

from src.campaigns.chain_service import LINK_KIND_UNSUBSCRIBE
from src.utils.config import settings

CHAIN_BUTTONS_MARKER = 'data-ma-chain-buttons="1"'
TEXT_CHAIN_BUTTONS_MARKER = "[CHAIN_BUTTONS]"

ChainButton: TypeAlias = tuple[str, str, str]

_PLACEHOLDER_RE = re.compile(
    r'<(?P<tag>div|td)\b(?P<attrs>[^>]*\bdata-ma-chain-buttons\s*=\s*["\']1["\'][^>]*)>.*?</(?P=tag)>',
    re.IGNORECASE | re.DOTALL,
)

_DEFAULT_WRAPPER_STYLE = "text-align:center;padding:8px 0"
_PREVIEW_ACTION_LABELS = ("Вариант 1", "Вариант 2")
_PREVIEW_UNSUBSCRIBE_LABEL = "Отписаться"
_ACTION_BUTTON_STYLE = (
    "display:inline-block;margin:0 4px;padding:8px 16px;background:#236348;color:#fff;"
    "text-decoration:none;border-radius:4px"
)
_UNSUBSCRIBE_BUTTON_STYLE = (
    "display:inline-block;margin:0;padding:0;background:transparent;color:#868e96;"
    "text-decoration:underline"
)
_PREVIEW_ACTION_STYLE = (
    "display:inline-block;margin:0 4px;padding:8px 16px;background:#236348;color:#fff;"
    "border-radius:4px"
)
_PREVIEW_UNSUBSCRIBE_STYLE = (
    "display:inline-block;margin:0;padding:0;background:transparent;color:#868e96;"
    "text-decoration:underline"
)
_UNSUBSCRIBE_WRAPPER_STYLE = "text-align:right;padding:12px 0 0"


def has_chain_button_placeholder(html: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(html or ""))


def _public_base_url() -> str:
    return str(getattr(settings, "public_base_url", "") or "http://localhost:9806").rstrip("/")


def _normalize_button(button: tuple[str, str] | ChainButton) -> ChainButton:
    if len(button) == 2:
        return (button[0], button[1], "custom")
    label, token, link_kind = button
    return (label, token, str(link_kind or "custom"))


def _split_buttons(buttons: list[tuple[str, str] | ChainButton]) -> tuple[list[ChainButton], list[ChainButton]]:
    action_buttons: list[ChainButton] = []
    unsubscribe_buttons: list[ChainButton] = []
    for raw in buttons:
        label, token, link_kind = _normalize_button(raw)
        if link_kind == LINK_KIND_UNSUBSCRIBE:
            unsubscribe_buttons.append((label, token, link_kind))
        else:
            action_buttons.append((label, token, link_kind))
    return action_buttons, unsubscribe_buttons


def _extract_wrapper_style(attrs: str) -> str:
    style_match = re.search(r'style\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE)
    if not style_match:
        return _DEFAULT_WRAPPER_STYLE
    preserved: list[str] = []
    for part in style_match.group(1).split(";"):
        token = part.strip()
        if token.startswith("text-align:") or token.startswith("padding") or token.startswith("margin"):
            preserved.append(token)
    return ";".join(preserved) if preserved else _DEFAULT_WRAPPER_STYLE


def _extract_action_wrapper_style(wrapper_style: str) -> str:
    preserved: list[str] = []
    for part in wrapper_style.split(";"):
        token = part.strip()
        if not token:
            continue
        if token.startswith("text-align:") or token.startswith("padding") or token.startswith("margin"):
            preserved.append(token)
    if not any(token.startswith("text-align:") for token in preserved):
        preserved.insert(0, "text-align:center")
    if not preserved:
        return _DEFAULT_WRAPPER_STYLE
    return ";".join(preserved)


def _render_action_buttons_html(action_buttons: list[ChainButton]) -> tuple[str, list[str]]:
    if not action_buttons:
        return "", []
    base = _public_base_url()
    text_parts: list[str] = []
    button_links: list[str] = []
    for label, token, _link_kind in action_buttons:
        link = f"{base}/chain/branch/{token}"
        safe_label = escape(label)
        button_links.append(f'<a href="{link}" style="{_ACTION_BUTTON_STYLE}">{safe_label}</a>')
        text_parts.append(f"{label}: {link}")
    return "".join(button_links), text_parts


def _render_unsubscribe_buttons_html(unsubscribe_buttons: list[ChainButton]) -> tuple[str, list[str]]:
    if not unsubscribe_buttons:
        return "", []
    base = _public_base_url()
    text_parts: list[str] = []
    button_links: list[str] = []
    for label, token, _link_kind in unsubscribe_buttons:
        link = f"{base}/chain/branch/{token}"
        safe_label = escape(label)
        button_links.append(f'<a href="{link}" style="{_UNSUBSCRIBE_BUTTON_STYLE}">{safe_label}</a>')
        text_parts.append(f"{label}: {link}")
    return "".join(button_links), text_parts


def build_chain_buttons_html(
    buttons: list[tuple[str, str] | ChainButton],
    *,
    wrapper_style: str = _DEFAULT_WRAPPER_STYLE,
) -> tuple[str, str]:
    if not buttons:
        return "", ""
    action_buttons, unsubscribe_buttons = _split_buttons(buttons)
    action_row, action_text = _render_action_buttons_html(action_buttons)
    unsubscribe_row, unsubscribe_text = _render_unsubscribe_buttons_html(unsubscribe_buttons)
    text_parts = action_text + unsubscribe_text

    html_parts: list[str] = []
    if action_row:
        action_wrapper = _extract_action_wrapper_style(wrapper_style)
        html_parts.append(f'<div style="{action_wrapper}"><p style="margin:0">{action_row}</p></div>')
    if unsubscribe_row:
        html_parts.append(
            f'<div style="{_UNSUBSCRIBE_WRAPPER_STYLE}"><p style="margin:0">{unsubscribe_row}</p></div>'
        )
    return "".join(html_parts), "\n".join(text_parts)


def build_chain_buttons_preview_html(*, wrapper_style: str = _DEFAULT_WRAPPER_STYLE) -> str:
    action_wrapper = _extract_action_wrapper_style(wrapper_style)
    action_row = "".join(
        f'<span style="{_PREVIEW_ACTION_STYLE}">{label}</span>' for label in _PREVIEW_ACTION_LABELS
    )
    unsubscribe_row = f'<span style="{_PREVIEW_UNSUBSCRIBE_STYLE}">{_PREVIEW_UNSUBSCRIBE_LABEL}</span>'
    return (
        f'<div style="{action_wrapper}"><p style="margin:0">{action_row}</p></div>'
        f'<div style="{_UNSUBSCRIBE_WRAPPER_STYLE}"><p style="margin:0">{unsubscribe_row}</p></div>'
    )


def inject_chain_buttons(
    html: str,
    text: str,
    buttons: list[tuple[str, str] | ChainButton],
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
