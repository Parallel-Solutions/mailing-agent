from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.generator.case_engine.overrides import lookup_override, upsert_override
from src.generator.config_generator import AGENT_MEMORY_AUTO_APPROVE_SAFE_INFLECTIONS
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
    if AGENT_MEMORY_AUTO_APPROVE_SAFE_INFLECTIONS:
        candidates.extend(auto_approve_safe_inflections(job_id))
    path = get_agent_memory_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in candidates:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return candidates


def auto_approve_safe_inflections(job_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_inflection_log(job_id)
    approvals: list[dict[str, Any]] = []
    for row in rows:
        decision = _safe_auto_approval_decision(row)
        if not decision["allowed"]:
            continue
        try:
            result = _upsert_auto_override(
                entity_type=decision["entity_type"],
                source_value=str(row.get("source_value") or ""),
                target_case=str(row.get("target_case") or ""),
                result_value=str(row.get("result_value") or ""),
            )
        except ValueError as exc:
            approvals.append(_auto_approval_record(row, status="rejected", reason=str(exc)))
            continue
        verified = _verify_auto_override(
            entity_type=result["entity_type"],
            source_value=result["source_value"],
            target_case=result["target_case"],
            expected_value=result["result_value"],
        )
        status = "auto_approved_verified" if verified["verified"] else "auto_approved_failed_self_check"
        approvals.append(
            _auto_approval_record(
                row,
                status=status,
                reason=decision["reason"] if verified["verified"] else verified["reason"],
                extra={
                    "entity_type": result["entity_type"],
                    "source_value": result["source_value"],
                    "result_value": result["result_value"],
                    "previous_value": result["previous_value"],
                    "verified_value": verified["actual_value"],
                },
            )
        )
    return approvals


def _upsert_auto_override(
    *,
    entity_type: str,
    source_value: str,
    target_case: str,
    result_value: str,
) -> dict[str, Any]:
    return upsert_override(
        entity_type=entity_type,
        source_value=source_value,
        target_case=target_case,
        result_value=result_value,
    )


def _verify_auto_override(
    *,
    entity_type: str,
    source_value: str,
    target_case: str,
    expected_value: str,
) -> dict[str, Any]:
    actual_value = lookup_override(entity_type, source_value, target_case)
    if actual_value == expected_value:
        return {
            "verified": True,
            "actual_value": actual_value,
            "reason": "lookup_override returned the newly approved value.",
        }
    return {
        "verified": False,
        "actual_value": actual_value,
        "reason": "Self-check failed: lookup_override did not return the approved value.",
    }


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
        "verified_value",
        "previous_value",
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


def _safe_auto_approval_decision(row: dict[str, Any]) -> dict[str, Any]:
    field = str(row.get("field") or "")
    source_value = str(row.get("source_value") or "").strip()
    result_value = str(row.get("result_value") or "").strip()
    target_case = str(row.get("target_case") or "").strip()
    method = str(row.get("method") or "").strip()
    confidence = str(row.get("confidence") or "").strip()
    warning = str(row.get("warning") or "").strip()

    entity_type = _field_to_entity_type(field)
    if not entity_type:
        return {"allowed": False, "entity_type": "", "reason": "unsupported_field"}
    if warning:
        return {"allowed": False, "entity_type": entity_type, "reason": "has_warning"}
    if method not in {"legacy_rule", "legacy_morph"}:
        return {"allowed": False, "entity_type": entity_type, "reason": "method_not_trusted_for_auto_approval"}
    if confidence in {"empty", "no_morph", "low"}:
        return {"allowed": False, "entity_type": entity_type, "reason": "low_confidence"}
    if not source_value or not result_value or source_value == result_value:
        return {"allowed": False, "entity_type": entity_type, "reason": "empty_or_unchanged_value"}
    if target_case not in {"genitive", "dative", "prepositional", "project_genitive"}:
        return {"allowed": False, "entity_type": entity_type, "reason": "unsupported_case"}
    return {
        "allowed": True,
        "entity_type": entity_type,
        "reason": "No warnings, trusted method, changed value, supported field and case.",
    }


def _field_to_entity_type(field: str) -> str:
    if field.startswith("MUN_NAME_"):
        return "municipality"
    if field.startswith("HEAD_FIO_"):
        return "fio"
    if field == "SUB_RF_1":
        return "subject_rf"
    if field == "MUN_R_NAME_1":
        return "municipal_district"
    if field == "ADM_NAME_1":
        return "administration"
    return ""


def _auto_approval_record(
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "candidate_type": "inflection_auto_override",
        "status": status,
        "source": "agent_memory",
        "row_id": row.get("row_id", ""),
        "field": row.get("field", ""),
        "source_value": row.get("source_value", ""),
        "result_value": row.get("result_value", ""),
        "method": row.get("method", ""),
        "confidence": row.get("confidence", ""),
        "reason": reason,
        "warning": row.get("warning", ""),
    }
    if extra:
        record.update(extra)
    return record


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
