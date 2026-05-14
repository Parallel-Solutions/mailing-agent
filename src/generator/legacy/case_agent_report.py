from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List

from openpyxl import Workbook


def _safe_str(value) -> str:
    if value is None:
        return ""
    return str(value)


def _build_detail_rows(results: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        summary = result.get("case_agent_summary", {}) or {}
        items = result.get("case_agent_items", []) or []
        for item in items:
            corrected_value = item.get("corrected_value")
            generated_value = item.get("generated_value")
            final_value = corrected_value if item.get("status") == "fix" else generated_value
            rows.append(
                {
                    "id": result.get("id"),
                    "mun_name": result.get("mun_name"),
                    "overall_status": result.get("case_agent_status"),
                    "mode": result.get("case_agent_mode"),
                    "field": item.get("field"),
                    "item_status": item.get("status"),
                    "source_value": item.get("source_value"),
                    "generated_value": generated_value,
                    "corrected_value": corrected_value,
                    "final_value": final_value,
                    "confidence": item.get("confidence"),
                    "comment": item.get("comment"),
                    "reviewed_fields_count": summary.get("reviewed_fields_count", 0),
                    "ok_count": summary.get("ok_count", 0),
                    "fix_count": summary.get("fix_count", 0),
                    "needs_review_count": summary.get("needs_review_count", 0),
                    "error": result.get("case_agent_error"),
                }
            )
    return rows


def _build_summary_rows(results: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        summary = result.get("case_agent_summary", {}) or {}
        rows.append(
            {
                "id": result.get("id"),
                "mun_name": result.get("mun_name"),
                "overall_status": result.get("case_agent_status"),
                "mode": result.get("case_agent_mode"),
                "reviewed_fields_count": summary.get("reviewed_fields_count", 0),
                "ok_count": summary.get("ok_count", 0),
                "fix_count": summary.get("fix_count", 0),
                "needs_review_count": summary.get("needs_review_count", 0),
                "error": result.get("case_agent_error"),
            }
        )
    return rows


def _write_csv(path: Path, rows: List[dict], headers: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(headers))
        writer.writeheader()
        for row in rows:
            writer.writerow({header: _safe_str(row.get(header)) for header in writer.fieldnames})


def _write_sheet(ws, rows: List[dict], headers: List[str]) -> None:
    ws.append(headers)
    for row in rows:
        ws.append([_safe_str(row.get(header)) for header in headers])
    ws.freeze_panes = "A2"
    for column_cells in ws.columns:
        max_length = max(len(_safe_str(cell.value)) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 60)


def save_case_agent_reports(output_dir: Path, results: List[dict]) -> None:
    summary_rows = _build_summary_rows(results)
    detail_rows = _build_detail_rows(results)

    summary_headers = [
        "id",
        "mun_name",
        "overall_status",
        "mode",
        "reviewed_fields_count",
        "ok_count",
        "fix_count",
        "needs_review_count",
        "error",
    ]
    detail_headers = [
        "id",
        "mun_name",
        "overall_status",
        "mode",
        "field",
        "item_status",
        "source_value",
        "generated_value",
        "corrected_value",
        "final_value",
        "confidence",
        "comment",
        "reviewed_fields_count",
        "ok_count",
        "fix_count",
        "needs_review_count",
        "error",
    ]

    _write_csv(output_dir / "case_agent_summary.csv", summary_rows, summary_headers)
    _write_csv(output_dir / "case_agent_details.csv", detail_rows, detail_headers)

    workbook = Workbook()
    summary_ws = workbook.active
    summary_ws.title = "summary"
    _write_sheet(summary_ws, summary_rows, summary_headers)

    details_ws = workbook.create_sheet("details")
    _write_sheet(details_ws, detail_rows, detail_headers)

    workbook.save(output_dir / "case_agent_report.xlsx")
