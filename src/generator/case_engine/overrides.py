from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import DATA_DIR


OVERRIDES_PATH = DATA_DIR / "knowledge" / "inflection_overrides.json"
ALLOWED_ENTITY_TYPES = {"municipality", "fio", "subject_rf", "municipal_district", "administration"}
ALLOWED_TARGET_CASES = {"genitive", "dative", "prepositional", "project_genitive"}


def _normalize_key(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


@lru_cache(maxsize=1)
def load_inflection_overrides(path: Path = OVERRIDES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def lookup_override(entity_type: str, source_value: str, target_case: str) -> str:
    data = load_inflection_overrides()
    section = data.get(entity_type)
    if not isinstance(section, dict):
        return ""

    normalized_source = _normalize_key(source_value)
    for raw_key, forms in section.items():
        if _normalize_key(raw_key) != normalized_source:
            continue
        if not isinstance(forms, dict):
            return ""
        value = forms.get(target_case)
        return str(value).strip() if value else ""
    return ""


def upsert_override(
    *,
    entity_type: str,
    source_value: str,
    target_case: str,
    result_value: str,
    path: Path = OVERRIDES_PATH,
) -> dict[str, Any]:
    entity_type = str(entity_type or "").strip()
    source_value = str(source_value or "").strip()
    target_case = str(target_case or "").strip()
    result_value = str(result_value or "").strip()

    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError(f"Unsupported entity_type: {entity_type}")
    if target_case not in ALLOWED_TARGET_CASES:
        raise ValueError(f"Unsupported target_case: {target_case}")
    if not source_value:
        raise ValueError("source_value is required")
    if not result_value:
        raise ValueError("result_value is required")

    data = load_inflection_overrides(path)
    if not isinstance(data, dict):
        data = {}

    section = data.setdefault(entity_type, {})
    if not isinstance(section, dict):
        section = {}
        data[entity_type] = section

    existing_key = ""
    normalized_source = _normalize_key(source_value)
    for raw_key in section:
        if _normalize_key(raw_key) == normalized_source:
            existing_key = raw_key
            break

    key = existing_key or source_value
    forms = section.get(key)
    if not isinstance(forms, dict):
        forms = {}
        section[key] = forms

    previous_value = str(forms.get(target_case) or "")
    forms[target_case] = result_value

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    load_inflection_overrides.cache_clear()

    return {
        "entity_type": entity_type,
        "source_value": key,
        "target_case": target_case,
        "result_value": result_value,
        "previous_value": previous_value,
        "created": not bool(previous_value),
    }
