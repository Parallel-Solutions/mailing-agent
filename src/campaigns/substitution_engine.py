"""Unified placeholder discovery and replacement for CampaignFlow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

from src.campaigns.substitution_context import SYSTEM_VARIABLE_ALIASES
from src.generator.generation.template_analysis import _norm_token

_BRACE_WORD = r"[a-zA-Z0-9_а-яА-ЯёЁ]+"
# Multi-word braces are opt-in (e.g. {{Имя Отчество}}); do not treat {{вид работ}} as one token.
_BRACE_TOKEN = rf"(?:{_BRACE_WORD}|Имя\s+Отчество)"
BRACE_RE = re.compile(rf"\{{\{{\s*({_BRACE_TOKEN})\s*\}}\}}")
MALFORMED_BRACE_RE = re.compile(rf"\{{{{3,}}\s*({_BRACE_TOKEN})\s*\}}" + r"{3,}")
ANY_BRACE_ARTIFACT_RE = re.compile(r"\{\{[^{}]{0,120}?\}\}")
BROKEN_BRACE_CANDIDATE_RE = re.compile(r"\{{2,}[^{}]+")
BARE_TOKEN_RE = re.compile(
    r"\b(?:ADM_NAME(?:_1)?|MUN_NAME(?:_[123])?|MUN_R_NAME(?:_1)?|SUB_RF(?:_1)?|"
    r"HEAD_FIO(?:_(?:1|2|SHORT))?|OUTGOING_NUMBER|CONTRACT_NUMBER|DATE|VALID_UNTIL|"
    r"DIRECTOR_NAME|PRICE_TOTAL|WORK_(?:TYPE(?:_LABEL)?|TITLE(?:_1|_NOMINATIVE)?|"
    r"SHORT_NAME|RESULT_NAME|SCOPE_FRAGMENT)|MUN_R_SCOPE_FRAGMENT|HEAD_MO_FRAGMENT|"
    r"POPULATION(?:_WITH_UNIT)?|ADRES|EMAIL(?:_OSN|_DOP)?|TEL_(?:OSN|DOP)|"
    r"REQUISITES(?:_[A-Z0-9]+)?|campaign_name|current_date|company|contact_name|email|region)\b"
)

COMPOUND_TOKENS: tuple[tuple[str, str], ...] = (
    ("MUN_R_NAME  SUB_RF_1", "MUN_R_SCOPE_FRAGMENT"),
    ("MUN_R_NAME SUB_RF_1", "MUN_R_SCOPE_FRAGMENT"),
    ("MUN_R_NAME  SUB_RF", "MUN_R_SCOPE_FRAGMENT"),
    ("MUN_R_NAME SUB_RF", "MUN_R_SCOPE_FRAGMENT"),
    ("MUN_NAME_2 MUN_R_NAME SUB_RF_1", "WORK_SCOPE_FRAGMENT"),
    ("MUN_NAME_2 MUN_R_NAME SUB_RF", "WORK_SCOPE_FRAGMENT"),
)

TERRITORY_PLACEHOLDER_NAMES = frozenset(
    {
        "MUN_NAME",
        "mun_name",
        "MUN_R_NAME",
        "SUB_RF",
        "MUN_NAME_1",
        "MUN_NAME_2",
        "MUN_R_NAME_1",
        "SUB_RF_1",
    }
)

ADMIN_PLACEHOLDER_NAMES = frozenset(
    {
        "ADM_NAME",
        "ADM_NAME_1",
    }
)

IDENTIFIER_VARIABLE_NAMES = frozenset(
    {
        _norm_token(name)
        for name in (
            "ид",
            "id",
            "номер",
            "number",
            "docnumber",
            "documentnumber",
            "documentid",
            "identifier",
            "идентификатор",
            "ref",
            "reference",
            "regnumber",
            "DOCUMENT_ID",
        )
    }
)


def is_identifier_variable(name: str) -> bool:
    return _norm_token(name) in IDENTIFIER_VARIABLE_NAMES


def template_has_identifier_placeholder(text: str) -> bool:
    for item in discover_placeholders(text):
        if is_identifier_variable(item.name):
            return True
    return False


@dataclass(frozen=True)
class PlaceholderInfo:
    token: str
    name: str
    kind: str


def _context_value(context: dict[str, str], name: str) -> str:
    if name in context:
        return str(context[name])
    upper = name.upper()
    if upper in context:
        return str(context[upper])
    canonical = SYSTEM_VARIABLE_ALIASES.get(name) or SYSTEM_VARIABLE_ALIASES.get(name.lower())
    if canonical:
        return str(context.get(canonical) or context.get(canonical.upper()) or "")

    from src.campaigns.placeholder_semantic import resolve_recipient_canonical, resolve_system_canonical

    semantic_canonical = resolve_system_canonical(name) or resolve_recipient_canonical(name)
    if semantic_canonical:
        return str(
            context.get(semantic_canonical) or context.get(semantic_canonical.upper()) or ""
        )
    return ""


def discover_placeholders(text: str) -> list[PlaceholderInfo]:
    if not text:
        return []

    found: dict[str, PlaceholderInfo] = {}
    for match in BRACE_RE.finditer(text):
        name = match.group(1)
        found[match.group(0)] = PlaceholderInfo(token=match.group(0), name=name, kind="brace")

    for token, canonical in COMPOUND_TOKENS:
        if token in text:
            found[token] = PlaceholderInfo(token=token, name=canonical, kind="compound")

    for match in BARE_TOKEN_RE.finditer(text):
        name = match.group(0)
        if name in found:
            continue
        found[name] = PlaceholderInfo(token=name, name=name, kind="bare")

    return sorted(found.values(), key=lambda item: len(item.token), reverse=True)


def discover_malformed_placeholders(text: str) -> list[PlaceholderInfo]:
    if not text:
        return []
    found: dict[str, PlaceholderInfo] = {}
    for match in MALFORMED_BRACE_RE.finditer(text):
        token = match.group(0)
        found[token] = PlaceholderInfo(token=token, name=match.group(1), kind="malformed")
    for item in discover_broken_brace_syntax(text):
        found[item.token] = item
    return sorted(found.values(), key=lambda item: len(item.token), reverse=True)


def html_to_review_text(html: str) -> str:
    if not html:
        return ""
    text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    text = text.replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _brace_edge_counts(token: str) -> tuple[int, int]:
    open_count = 0
    for char in token:
        if char == "{":
            open_count += 1
        else:
            break
    close_count = 0
    for char in reversed(token):
        if char == "}":
            close_count += 1
        else:
            break
    return open_count, close_count


def discover_broken_brace_syntax(text: str) -> list[PlaceholderInfo]:
    if not text:
        return []
    found: dict[str, PlaceholderInfo] = {}
    idx = 0
    while idx < len(text):
        if idx + 1 < len(text) and text[idx : idx + 2] == "{{":
            start = idx
            probe = idx
            while probe < len(text) and text[probe] == "{":
                probe += 1
            end = probe
            while end < len(text) and text[end] not in "{}":
                end += 1
            while end < len(text) and text[end] == "}":
                end += 1
            token = text[start:end]
            open_count, close_count = _brace_edge_counts(token)
            inner = token[open_count : len(token) - close_count if close_count else len(token)].strip()
            if open_count >= 2 and open_count != close_count:
                found[token] = PlaceholderInfo(token=token, name=inner or token, kind="malformed")
            idx = max(end, start + 1)
            continue
        idx += 1
    return sorted(found.values(), key=lambda item: len(item.token), reverse=True)


def discover_brace_artifacts(text: str) -> list[PlaceholderInfo]:
    if not text:
        return []
    found: dict[str, PlaceholderInfo] = {}
    for match in ANY_BRACE_ARTIFACT_RE.finditer(text):
        token = match.group(0)
        inner = token[2:-2].strip()
        found[token] = PlaceholderInfo(token=token, name=inner or token, kind="artifact")
    return sorted(found.values(), key=lambda item: len(item.token), reverse=True)


@dataclass(frozen=True)
class TemplateDefect:
    token: str
    name: str
    kind: str


def find_template_defects(text: str, *, source: str = "rendered") -> list[TemplateDefect]:
    if not text:
        return []
    defects: dict[str, TemplateDefect] = {}
    if source == "original":
        for item in discover_malformed_placeholders(text):
            defects[item.token] = TemplateDefect(token=item.token, name=item.name, kind="malformed")
        return sorted(defects.values(), key=lambda item: len(item.token), reverse=True)

    review_text = html_to_review_text(text) if "<" in text and ">" in text else text
    for item in discover_malformed_placeholders(review_text):
        defects[item.token] = TemplateDefect(token=item.token, name=item.name, kind="malformed")
    for item in discover_brace_artifacts(review_text):
        defects[item.token] = TemplateDefect(token=item.token, name=item.name, kind="artifact")
    for item in discover_placeholders(review_text):
        if item.kind in {"bare", "compound"} and item.token in review_text:
            defects[item.token] = TemplateDefect(token=item.token, name=item.name, kind="unresolved")
    return sorted(defects.values(), key=lambda item: len(item.token), reverse=True)


def _resolve_canonical_name(name: str) -> str | None:
    from src.campaigns.placeholder_semantic import resolve_recipient_canonical, resolve_system_canonical

    clean = str(name or "").strip()
    if not clean:
        return None
    return resolve_system_canonical(clean) or resolve_recipient_canonical(clean)


def resolve_placeholder_canonical(name: str) -> str | None:
    return _resolve_canonical_name(name)


def placeholder_fragment_inner_name(fragment: str) -> str:
    clean = str(fragment or "").strip()
    if clean.startswith("{{"):
        return clean.strip("{}").strip()
    if clean.startswith("{"):
        return clean[1:].strip()
    return clean


def is_blocking_placeholder_defect(defect: TemplateDefect) -> bool:
    if defect.kind == "malformed":
        return True
    if defect.kind in {"artifact", "unresolved"}:
        return resolve_placeholder_canonical(defect.name) is None
    return True


def is_resolvable_placeholder_fragment(fragment: str, kind: str) -> bool:
    if kind == "malformed":
        return False
    if kind in {"artifact", "unresolved"}:
        name = placeholder_fragment_inner_name(fragment) if kind == "artifact" else str(fragment or "").strip()
        return resolve_placeholder_canonical(name) is not None
    return False


def is_blocking_placeholder_fragment(fragment: str, kind: str) -> bool:
    if kind not in {"artifact", "malformed", "unresolved"}:
        return True
    if kind == "malformed":
        return True
    return not is_resolvable_placeholder_fragment(fragment, kind)


def _has_context_key(context: dict[str, str], name: str) -> bool:
    if name in context or name.upper() in context:
        return True
    canonical = SYSTEM_VARIABLE_ALIASES.get(name) or SYSTEM_VARIABLE_ALIASES.get(name.lower())
    if canonical and (canonical in context or canonical.upper() in context):
        return True

    semantic_canonical = _resolve_canonical_name(name)
    if semantic_canonical and (
        semantic_canonical in context or semantic_canonical.upper() in context
    ):
        return True
    return False


def _artifact_replacement_value(context: dict[str, str], item: PlaceholderInfo) -> str:
    if item.token in context:
        return str(context[item.token])
    if item.name in context:
        return str(context[item.name])
    canonical = _resolve_canonical_name(item.name)
    if canonical and _has_context_key(context, canonical):
        return _context_value(context, canonical)
    if _has_context_key(context, item.name):
        return _context_value(context, item.name)
    return ""


def _resolve_territory_canonical(name: str) -> str:
    canonical = SYSTEM_VARIABLE_ALIASES.get(name) or SYSTEM_VARIABLE_ALIASES.get(name.lower())
    if canonical:
        return str(canonical).upper()
    return str(name or "").upper()


def _is_territory_placeholder(name: str) -> bool:
    canonical = _resolve_territory_canonical(name)
    return canonical in TERRITORY_PLACEHOLDER_NAMES or name in TERRITORY_PLACEHOLDER_NAMES


def _is_admin_placeholder(name: str) -> bool:
    canonical = _resolve_territory_canonical(name)
    return canonical in ADMIN_PLACEHOLDER_NAMES or name in ADMIN_PLACEHOLDER_NAMES


def _looks_like_admin_title(value: str) -> bool:
    clean = str(value or "").strip().casefold()
    return clean.startswith(("администрация", "администрации"))


def _should_apply_admin_case(name: str, value: str) -> bool:
    if _is_admin_placeholder(name):
        return True
    if name.lower() == "company" and _looks_like_admin_title(value):
        return True
    return False


def _placeholder_is_sentence_start(text: str, token: str) -> bool:
    index = text.find(token)
    if index < 0:
        return False
    prefix = text[:index].rstrip()
    if not prefix:
        return True
    return bool(re.search(r"[.!?](?:\s|$)", prefix)) or prefix.endswith(("\n", "\r"))


def _placeholder_in_territory_genitive_context(text: str, token: str) -> bool:
    index = text.find(token)
    if index < 0:
        return False
    return text[:index].rstrip().casefold().endswith("для территории")


def _adapt_territory_value_case(value: str, *, name: str, text: str, token: str) -> str:
    from src.generator.generation.transforms import _normalize_mo_name_case, normalize_russian_geo_admin_case

    canonical = _resolve_territory_canonical(name)
    clean = str(value or "").strip()
    if not clean:
        return clean

    if _placeholder_in_territory_genitive_context(text, token):
        if canonical in {"MUN_NAME", "MUN_NAME"} or name.lower() == "mun_name":
            return _normalize_mo_name_case(clean)
        return normalize_russian_geo_admin_case(clean)

    if canonical in {"MUN_NAME"} or name.lower() == "mun_name":
        normalized = _normalize_mo_name_case(clean)
    elif canonical in {"MUN_R_NAME", "SUB_RF", "MUN_R_NAME_1", "SUB_RF_1", "MUN_NAME_1", "MUN_NAME_2"}:
        normalized = normalize_russian_geo_admin_case(clean)
    else:
        normalized = clean

    if _placeholder_is_sentence_start(text, token):
        if normalized == normalized.lower():
            return normalized[:1].upper() + normalized[1:]
        return normalized

    if normalized and normalized[0].isupper():
        return normalized[0].lower() + normalized[1:]
    return normalized


def _adapt_admin_value_case(value: str, *, text: str, token: str) -> str:
    from src.generator.generation.transforms import normalize_russian_geo_admin_case

    clean = normalize_russian_geo_admin_case(str(value or "").strip())
    if not clean:
        return clean

    in_territory_context = _placeholder_in_territory_genitive_context(text, token)
    at_sentence_start = _placeholder_is_sentence_start(text, token)
    words = clean.split()
    if words and words[0].casefold() in {"администрация", "администрации"}:
        if in_territory_context or not at_sentence_start:
            words[0] = words[0].lower()
        elif at_sentence_start:
            words[0] = words[0][:1].upper() + words[0][1:].lower()
        clean = " ".join(words)
    elif at_sentence_start and clean == clean.lower():
        clean = clean[:1].upper() + clean[1:]
    return clean


def build_replacement_pairs(context: dict[str, str], text: str) -> list[tuple[str, str]]:
    pairs: dict[str, str] = {}
    for item in discover_placeholders(text):
        if not _has_context_key(context, item.name):
            continue
        value = _context_value(context, item.name)
        if _is_territory_placeholder(item.name):
            value = _adapt_territory_value_case(value, name=item.name, text=text, token=item.token)
        elif _should_apply_admin_case(item.name, value):
            value = _adapt_admin_value_case(value, text=text, token=item.token)
        pairs[item.token] = value

    for item in discover_brace_artifacts(text):
        if item.token in pairs:
            continue
        value = _artifact_replacement_value(context, item)
        if value and _is_territory_placeholder(item.name):
            value = _adapt_territory_value_case(value, name=item.name, text=text, token=item.token)
        elif value and _should_apply_admin_case(item.name, value):
            value = _adapt_admin_value_case(value, text=text, token=item.token)
        if value:
            pairs[item.token] = value

    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


def _territory_genitive_replacement(name: str, context: dict[str, str]) -> str | None:
    clean = str(name or "").strip()
    if not clean:
        return None

    mun_name_1 = _context_value(context, "MUN_NAME_1")
    mun_name = _context_value(context, "MUN_NAME") or _context_value(context, "company")
    if mun_name_1 and mun_name and clean.casefold() == mun_name.casefold():
        return mun_name_1

    from src.generator.inflection.inflect import inflect_mun_name_genitive

    inflected = inflect_mun_name_genitive(clean).value
    if inflected and inflected.casefold() != clean.casefold():
        return inflected
    return None


def _should_fix_territory_nominative(name: str, context: dict[str, str]) -> bool:
    from src.campaigns.text_local_review import NOMINATIVE_SETTLEMENT_TAIL_RE

    clean = str(name or "").strip()
    if not clean:
        return False
    if NOMINATIVE_SETTLEMENT_TAIL_RE.search(clean):
        return True
    mun_name = _context_value(context, "MUN_NAME") or _context_value(context, "company")
    if mun_name and clean.casefold() == mun_name.casefold():
        return True
    return _territory_genitive_replacement(clean, context) is not None


def _apply_territory_genitive_fix(text: str, context: dict[str, str]) -> str:
    from src.campaigns.text_local_review import TERRITORY_NOMINATIVE_RE

    def replacer(match: re.Match[str]) -> str:
        name = str(match.group("name") or "").strip()
        if not _should_fix_territory_nominative(name, context):
            return match.group(0)
        replacement = _territory_genitive_replacement(name, context)
        if not replacement:
            return match.group(0)
        return f"для территории {replacement}"

    return TERRITORY_NOMINATIVE_RE.sub(replacer, text)


def render_text(text: str, context: dict[str, str]) -> str:
    if not text:
        return text
    rendered = text
    for token, value in build_replacement_pairs(context, text):
        rendered = rendered.replace(token, value)
    return _apply_territory_genitive_fix(rendered, context)


def resolve_context_value(context: dict[str, str], name: str) -> str:
    return _context_value(context, name)


def find_unresolved_placeholders(text: str) -> list[str]:
    if not text:
        return []
    review_text = html_to_review_text(text) if "<" in text and ">" in text else text
    seen: set[str] = set()
    ordered: list[str] = []
    for source_text in (text, review_text):
        for item in discover_malformed_placeholders(source_text):
            if item.token not in seen:
                seen.add(item.token)
                ordered.append(item.token)
        for item in discover_brace_artifacts(source_text):
            if any(item.token in token and item.token != token for token in seen):
                continue
            if item.token not in seen:
                seen.add(item.token)
                ordered.append(item.token)
        for item in discover_placeholders(source_text):
            if item.kind in {"bare", "compound"} and item.token in source_text and item.token not in seen:
                seen.add(item.token)
                ordered.append(item.token)
    return ordered
