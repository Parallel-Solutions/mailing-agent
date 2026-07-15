from __future__ import annotations

import re


def normalize_phone_for_auto_call(raw: str) -> str | None:
    digits = re.sub(r"\D+", "", str(raw or ""))
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        return None
    return digits


def collect_normalized_phones(*values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for part in re.split(r"[;\n,]+", text):
            normalized = normalize_phone_for_auto_call(part)
            if normalized and normalized not in result:
                result.append(normalized)
    return result
