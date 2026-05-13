from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.generator.inflection_report import load_inflection_log
from src.jobs import resolve_state_path
from src.jobs.storage import resolve_job_paths


MEMORY_JSONL_NAME = "agent_memory_candidates.jsonl"
MEMORY_CSV_NAME = "agent_memory_candidates.csv"


def get_agent_memory_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / MEMORY_JSONL_NAME


def get_agent_memory_csv_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / MEMORY_CSV_NAME


def build_learning_candidates(job_id: str | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(_build_inflection_candidates(job_id))
    candidates.extend(_build_philologist_candidates(job_id))
    return candidates


def save_learning_memory(job_id: str | None = None) -> list[dict[str, Any]]:
    candidates = build_learning_candidates(job_id)
    path = get_agent_memory_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in candidates:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return candidates


def save_learning_memory_csv(candidates: list[dict[str, Any]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "candidate_type",
        "status",
        "source",
        "row_id",
        "document",
        "location",
        "field",
        "source_value",
        "result_value",
        "suggestion",
        "method",
        "confidence",
        "issue",
        "reason",
        "warning",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in candidates:
            writer.writerow({field: item.get(field, "") for field in fieldnames})


def _build_inflection_candidates(job_id: str | None) -> list[dict[str, Any]]:
    rows = load_inflection_log(job_id)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        warning = str(row.get("warning") or "").strip()
        method = str(row.get("method") or "").strip()
        confidence = str(row.get("confidence") or "").strip()
        needs_review = bool(warning) or method == "fallback" or confidence in {"empty", "no_morph", "low"}
        if not needs_review:
            continue
        candidates.append(
            {
                "candidate_type": "inflection_override_or_rule",
                "status": "needs_human_review",
                "source": "case_engine",
                "row_id": row.get("row_id", ""),
                "field": row.get("field", ""),
                "source_value": row.get("source_value", ""),
                "result_value": row.get("result_value", ""),
                "method": method,
                "confidence": confidence,
                "reason": row.get("reason", ""),
                "warning": warning,
            }
        )
    return candidates


def _build_philologist_candidates(job_id: str | None) -> list[dict[str, Any]]:
    state = _load_philologist_state(job_id)
    candidates: list[dict[str, Any]] = []
    for document in state.get("documents") or []:
        if not isinstance(document, dict):
            continue
        for fix in document.get("applied_fixes") or []:
            if isinstance(fix, dict):
                candidates.append(_philologist_fix_candidate(document, fix, applied=True))
        for fix in document.get("skipped_fixes") or []:
            if isinstance(fix, dict):
                candidates.append(_philologist_fix_candidate(document, fix, applied=False))
    return candidates


def _philologist_fix_candidate(document: dict[str, Any], fix: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    return {
        "candidate_type": "philology_safe_fix" if applied else "philology_rule_candidate",
        "status": "observed" if applied else "needs_human_review",
        "source": "philologist",
        "row_id": document.get("row_id", ""),
        "document": document.get("name", ""),
        "location": fix.get("location", ""),
        "suggestion": fix.get("suggestion", ""),
        "issue": fix.get("issue", ""),
        "reason": fix.get("mode", "") if applied else fix.get("reason", ""),
    }


def _load_philologist_state(job_id: str | None) -> dict[str, Any]:
    path = resolve_state_path("philologist", job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
