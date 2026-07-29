"""Deterministic cleanup for municipality administration names.

The source datasets contain both municipality names and full legal entity names.
Some rows mix the two and produce constructions such as
``Администрации муниципального образования «Администрация ...»``.  The helpers
in this module are deliberately independent from the inflection engine so they
can also protect stored campaign contexts and already compiled templates.
"""

from __future__ import annotations

import re


_ADMIN_WORD_RE = r"администраци(?:я|и)"
_QUOTED_MUNICIPALITY_RE = re.compile(
    rf"(?P<outer>{_ADMIN_WORD_RE})\s+"
    r"(?:муниципального\s+образования|мо)\s*"
    r"(?P<open>[«\"„“])\s*"
    rf"{_ADMIN_WORD_RE}\s+"
    r"(?P<name>[^»\"”]+?)\s*"
    r"(?P<close>[»\"”])",
    re.IGNORECASE,
)
_UNQUOTED_NESTED_PREFIX_RE = re.compile(
    rf"\b(?P<outer>{_ADMIN_WORD_RE})\s+"
    r"(?:муниципального\s+образования|мо)\s+"
    rf"{_ADMIN_WORD_RE}\s+",
    re.IGNORECASE,
)
_DIRECT_DUPLICATE_RE = re.compile(
    rf"\b(?P<outer>{_ADMIN_WORD_RE})\s+{_ADMIN_WORD_RE}\s+",
    re.IGNORECASE,
)
_OUTER_MUNICIPALITY_RE = re.compile(
    rf"^\s*{_ADMIN_WORD_RE}\s+"
    r"(?:муниципального\s+образования|мо)\s*"
    r"[«\"„“]\s*(?P<name>.+?)\s*[»\"”]\s*$",
    re.IGNORECASE,
)
_LEADING_ADMINISTRATION_RE = re.compile(
    rf"^\s*{_ADMIN_WORD_RE}\s+",
    re.IGNORECASE,
)
_MUNICIPALITY_QUOTE_SPACING_RE = re.compile(
    r"\b(?P<prefix>муниципального\s+образования)"
    r"(?P<quote>[«\"„“])(?=\s*[А-ЯЁа-яё])",
    re.IGNORECASE,
)
_DISTRICT_LEVEL_RE = re.compile(
    r"\b(?:"
    r"район(?:а|у|ом|е)?"
    r"|округ(?:а|у|ом|е)?"
    r")\b",
    re.IGNORECASE,
)
_DISTRICT_SUBENTITY_RE = re.compile(
    r"\b(?:поселени[еяюем]|сельсовет(?:а|у|ом|е)?|поссовет(?:а|у|ом|е)?)\b",
    re.IGNORECASE,
)


def _normalize_spaces(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def contains_nested_administration(value: object) -> bool:
    """Return whether one administration title is wrapped in another."""

    text = _normalize_spaces(value)
    return bool(
        _QUOTED_MUNICIPALITY_RE.search(text)
        or _UNQUOTED_NESTED_PREFIX_RE.search(text)
        or _DIRECT_DUPLICATE_RE.search(text)
    )


def normalize_administration_mentions(value: object) -> str:
    """Remove nested administration titles anywhere in a generated text."""

    text = str(value or "")
    text = _MUNICIPALITY_QUOTE_SPACING_RE.sub(
        lambda match: f"{match.group('prefix')} {match.group('quote')}",
        text,
    )

    previous = None
    while text != previous:
        previous = text
        text = _QUOTED_MUNICIPALITY_RE.sub(
            lambda match: f"{match.group('outer')} {match.group('name').strip()}",
            text,
        )
        text = _UNQUOTED_NESTED_PREFIX_RE.sub(
            lambda match: f"{match.group('outer')} ",
            text,
        )
        text = _DIRECT_DUPLICATE_RE.sub(
            lambda match: f"{match.group('outer')} ",
            text,
        )
    return text


def normalize_administration_recipient(value: object) -> str:
    """Canonicalize one complete recipient value without changing its case."""

    return _normalize_spaces(normalize_administration_mentions(value))


def extract_administration_entity_name(value: object) -> str:
    """Extract the municipality/district phrase from a full administration name."""

    original = _normalize_spaces(value)
    if not original:
        return ""

    normalized = normalize_administration_recipient(original)
    outer_match = _OUTER_MUNICIPALITY_RE.match(normalized)
    if outer_match:
        normalized = _normalize_spaces(outer_match.group("name"))

    previous = None
    while normalized and normalized != previous:
        previous = normalized
        normalized = _LEADING_ADMINISTRATION_RE.sub("", normalized, count=1).strip()
        normalized = normalized.strip("«»\"„“ ").strip()
    return normalized


def is_district_level_entity_name(value: object) -> bool:
    """Recognize districts and municipal/city okrugs, but not settlements."""

    entity_name = extract_administration_entity_name(value)
    if not entity_name or _DISTRICT_SUBENTITY_RE.search(entity_name):
        return False
    return bool(_DISTRICT_LEVEL_RE.search(entity_name))


def format_administration_recipient(value: object) -> str:
    """Canonicalize a recipient and capitalize its first alphabetic character."""

    text = normalize_administration_recipient(value)
    for index, char in enumerate(text):
        if char.isalpha():
            return f"{text[:index]}{char.upper()}{text[index + 1:]}"
    return text
