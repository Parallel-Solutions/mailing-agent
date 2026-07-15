from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


FIELD_NAME_PATTERN = r"[A-Z][A-Z0-9_]{1,63}"
PLACEHOLDER_RE = re.compile(r"{{\s*(?P<name>" + FIELD_NAME_PATTERN + r")\s*}}")
PLACEHOLDER_LIKE_RE = re.compile(r"{{[^{}]*}}")


class TemplateProtocolError(ValueError):
    pass


def find_fields(text: str) -> tuple[str, ...]:
    return tuple(match.group("name") for match in PLACEHOLDER_RE.finditer(text or ""))


def validate_placeholder_syntax(text: str) -> None:
    valid_spans = {match.span() for match in PLACEHOLDER_RE.finditer(text or "")}
    invalid = [match.group(0) for match in PLACEHOLDER_LIKE_RE.finditer(text or "") if match.span() not in valid_spans]
    if invalid:
        samples = ", ".join(repr(item) for item in invalid[:5])
        raise TemplateProtocolError(f"Invalid placeholder syntax: {samples}")


def normalize_context(context: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).strip().upper(): "" if value is None else str(value) for key, value in context.items()}


def require_context(fields: tuple[str, ...] | list[str], context: Mapping[str, Any]) -> dict[str, str]:
    normalized = normalize_context(context)
    missing = sorted(field for field in set(fields) if field not in normalized)
    if missing:
        raise TemplateProtocolError("Missing template values: " + ", ".join(missing))
    return normalized


def replace_placeholders(text: str, context: Mapping[str, Any], *, fields: tuple[str, ...] | None = None) -> str:
    required = fields if fields is not None else find_fields(text)
    normalized = require_context(required, context)
    rendered = PLACEHOLDER_RE.sub(lambda match: normalized[match.group("name")], text)
    validate_placeholder_syntax(rendered)
    if PLACEHOLDER_LIKE_RE.search(rendered):
        raise TemplateProtocolError("Rendered output still contains placeholders")
    return rendered
