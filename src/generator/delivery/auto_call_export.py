from __future__ import annotations

from pathlib import Path

from src.generator.delivery.phone_normalize import collect_normalized_phones
from src.generator.delivery.sender_agent import _resolve_sender_data_xlsx_path
from src.generator.generation.excel_io import load_rows
from src.web.consent_sales_service import PHONE_EXTRA_KEYS, PHONE_PRIMARY_KEYS


def build_auto_call_phone_numbers(job_id: str | None) -> list[str]:
    try:
        data_path = _resolve_sender_data_xlsx_path(job_id)
        if not data_path.exists():
            return []
        _, _, rows = load_rows(data_path)
    except Exception:
        return []

    phones: list[str] = []
    for row in rows:
        raw_values = [str(row.get(key) or "") for key in (*PHONE_PRIMARY_KEYS, *PHONE_EXTRA_KEYS) if key in row]
        for phone in collect_normalized_phones(*raw_values):
            if phone not in phones:
                phones.append(phone)
    return phones


def write_auto_call_csv(path: Path, phones: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write("phone_number\n")
        for phone in phones:
            handle.write(f"{phone}\n")
