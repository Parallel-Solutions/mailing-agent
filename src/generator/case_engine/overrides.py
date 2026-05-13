from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.generator.config_generator import DATA_DIR


OVERRIDES_PATH = DATA_DIR / "knowledge" / "inflection_overrides.json"


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
