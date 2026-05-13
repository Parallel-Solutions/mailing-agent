from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.jobs.storage import resolve_job_paths


def get_inflection_log_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return job_paths.root_dir / "state" / "inflection_log.jsonl"
    return job_paths.root_dir / "state" / "inflection_log.jsonl"


def load_inflection_log(job_id: str | None = None) -> list[dict[str, Any]]:
    log_path = get_inflection_log_path(job_id)
    if not log_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def summarize_inflection_log(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_method: dict[str, int] = {}
    by_field: dict[str, int] = {}
    warning_rows: list[dict[str, Any]] = []

    for row in rows:
        method = str(row.get("method") or "unknown")
        field = str(row.get("field") or "unknown")
        by_method[method] = by_method.get(method, 0) + 1
        by_field[field] = by_field.get(field, 0) + 1
        if str(row.get("warning") or "").strip():
            warning_rows.append(row)

    return {
        "total": len(rows),
        "by_method": by_method,
        "by_field": by_field,
        "warning_count": len(warning_rows),
        "warning_rows": warning_rows,
    }


def format_inflection_report(rows: list[dict[str, Any]], *, limit: int = 12) -> str:
    summary = summarize_inflection_log(rows)
    if not rows:
        return "Журнал склонений пока не создан. Сначала запустите генератор."

    by_method = summary["by_method"]
    lines = [
        "Журнал склонений:",
        (
            f"Всего проверок: {summary['total']}. "
            f"Словарь: {by_method.get('override', 0)}, "
            f"правила: {by_method.get('legacy_rule', 0)}, "
            f"морфология: {by_method.get('legacy_morph', 0)}, "
            f"fallback: {by_method.get('fallback', 0)}, "
            f"предупреждений: {summary['warning_count']}."
        ),
        "",
        "Примеры решений:",
    ]

    for item in rows[:limit]:
        warning = str(item.get("warning") or "").strip()
        warning_suffix = f" | warning: {warning}" if warning else ""
        reason = str(item.get("reason") or "").strip()
        reason_suffix = f" | reason: {reason}" if reason else ""
        lines.append(
            "- "
            f"строка {item.get('row_id')}, {item.get('field')}: "
            f"`{item.get('source_value')}` -> `{item.get('result_value')}` "
            f"({item.get('method')}, {item.get('confidence')})"
            f"{warning_suffix}"
            f"{reason_suffix}"
        )

    if len(rows) > limit:
        lines.append(f"...и ещё {len(rows) - limit} записей в полном журнале.")

    return "\n".join(lines)


def save_inflection_csv(rows: list[dict[str, Any]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "row_id",
        "field",
        "source_field",
        "source_value",
        "result_value",
        "target_case",
        "method",
        "confidence",
        "warning",
        "reason",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
