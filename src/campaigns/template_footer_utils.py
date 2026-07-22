"""Strip service metadata footers from email template bodies."""

from __future__ import annotations

import re

_SERVICE_PLACEHOLDERS = ("{{email}}", "{{region}}", "{{campaign_name}}")

_SERVICE_PREFIXES = (
    "Контакт для связи:",
    "Контакт:",
    "Регион:",
    "С уважением ·",
    "Связаться:",
)

_MUTED_P_RE = re.compile(
    r'<p[^>]*style=["\'][^"\']*margin:0[^"\']*font-size:13px[^"\']*["\'][^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)

_INLINE_CONTACT_P_RE = re.compile(
    r"<p[^>]*style=['\"]margin:0['\"][^>]*>\s*Контакт:\s*\{\{email\}\}\s*</p>",
    re.IGNORECASE,
)

_FOOTER_TR_RE = re.compile(
    r"<tr><td[^>]*>(.*?)</td></tr>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_service_metadata_content(content: str) -> bool:
    text = _strip_html(content) if "<" in content else content.strip()
    if not text:
        return False

    if "{{contact_name}}" in text and not any(p in text for p in _SERVICE_PLACEHOLDERS):
        return False

    has_service_placeholder = any(p in text for p in _SERVICE_PLACEHOLDERS)
    has_service_prefix = any(prefix in text for prefix in _SERVICE_PREFIXES)

    if "©" in text and "{{campaign_name}}" in text:
        return True

    if "{{campaign_name}}" in text and "{{region}}" in text and "{{contact_name}}" not in text:
        return True

    if has_service_placeholder and has_service_prefix:
        return True

    if has_service_placeholder and " · " in text:
        placeholder_count = sum(1 for p in _SERVICE_PLACEHOLDERS if p in text)
        if placeholder_count >= 2:
            return True

    if text.startswith("{{email}}") and "{{campaign_name}}" in text:
        return True

    if text.startswith("С уважением") and has_service_placeholder:
        return True

    return False


def strip_email_metadata_footer(text: str) -> str:
    if not text or not text.strip():
        return text

    result = text

    def _replace_muted_p(match: re.Match[str]) -> str:
        if _is_service_metadata_content(match.group(1)):
            return ""
        return match.group(0)

    result = _MUTED_P_RE.sub(_replace_muted_p, result)
    result = _INLINE_CONTACT_P_RE.sub("", result)

    def _replace_footer_tr(match: re.Match[str]) -> str:
        inner = match.group(1)
        inner_text = _strip_html(inner)
        if not _is_service_metadata_content(inner_text):
            return match.group(0)
        if "{{contact_name}}" in inner_text and "{{campaign_name}}" not in inner_text:
            return match.group(0)
        return ""

    result = _FOOTER_TR_RE.sub(_replace_footer_tr, result)

    if not re.search(r"<[a-z]", result, re.IGNORECASE):
        lines = [line for line in result.splitlines() if not _is_service_metadata_content(line)]
        result = "\n".join(lines)

    return result
