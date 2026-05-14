from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generator.inflection.ai_case_agent import collect_case_reviews
from src.generator.generation.config_generator import DATA_XLSX_PATH
from src.generator.generation.excel_io import load_rows
from src.generator.generation.transforms import build_document_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare rows for native n8n case-agent flow.")
    parser.add_argument("--start", type=int, default=1, help="1-based first row index")
    parser.add_argument("--end", type=int, default=1, help="1-based last row index")
    parser.add_argument("--outgoing-start", type=int, default=101, help="Starting outgoing number")
    parser.add_argument("--xlsx", default=str(DATA_XLSX_PATH), help="Path to source XLSX")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        print(json.dumps({"ok": False, "error": f"Excel file not found: {xlsx_path}"}, ensure_ascii=False, indent=2))
        return 1

    _, _, rows = load_rows(xlsx_path)
    start = max(1, args.start)
    end = max(start, min(len(rows), args.end))

    items: list[dict] = []
    for offset, row in enumerate(rows[start - 1 : end], start=0):
        outgoing_number = args.outgoing_start + start - 1 + offset
        context = build_document_context(row, outgoing_number=outgoing_number)
        reviews = collect_case_reviews(row, context)
        items.append(
            {
                "id": row.get("ID"),
                "row_index": row.get("_row_index"),
                "mun_name": row.get("MUN_NAME"),
                "outgoing_number": outgoing_number,
                "row": row,
                "context": context,
                "reviews": [
                    {
                        "field": review.field,
                        "source_value": review.source_value,
                        "generated_value": review.generated_value,
                        "target_case": review.target_case,
                        "context_sentence": review.context_sentence,
                        "slot_instruction": review.slot_instruction,
                        "slot_label": review.slot_label,
                    }
                    for review in reviews
                ],
            }
        )

    payload = {
        "ok": True,
        "xlsx_path": str(xlsx_path),
        "selected_range": {"start": start, "end": end},
        "items": items,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
