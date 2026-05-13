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
QUARANTINE_JSONL_NAME = "agent_quarantine.jsonl"
QUARANTINE_CSV_NAME = "agent_quarantine.csv"
AGENT_REPORT_NAME = "agent_report.txt"


def get_agent_memory_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / MEMORY_JSONL_NAME


def get_agent_memory_csv_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / MEMORY_CSV_NAME


def get_agent_quarantine_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / QUARANTINE_JSONL_NAME


def get_agent_quarantine_csv_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / QUARANTINE_CSV_NAME


def get_agent_report_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    return job_paths.root_dir / "state" / AGENT_REPORT_NAME


def build_learning_candidates(job_id: str | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(_build_inflection_candidates(job_id))
    candidates.extend(_build_philologist_candidates(job_id))
    return candidates


def save_learning_memory(job_id: str | None = None) -> list[dict[str, Any]]:
    candidates = build_learning_candidates(job_id)
    if AGENT_MEMORY_AUTO_APPROVE_SAFE_INFLECTIONS:
        candidates.extend(auto_approve_safe_inflections(job_id))
    quarantine = build_quarantine_items(job_id)
    save_quarantine(job_id, quarantine)
    path = get_agent_memory_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in candidates:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    save_agent_report(job_id, candidates=candidates, quarantine=quarantine)
    return candidates


def build_agent_report(
    job_id: str | None = None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    quarantine: list[dict[str, Any]] | None = None,
) -> str:
    candidates = build_learning_candidates(job_id) if candidates is None else candidates
    quarantine = build_quarantine_items(job_id) if quarantine is None else quarantine
    inflection_rows = load_inflection_log(job_id)
    philologist_state = _load_philologist_state(job_id)

    auto_verified = [
        item for item in candidates
        if item.get("candidate_type") == "inflection_auto_override"
        and item.get("status") == "auto_approved_verified"
    ]
    auto_failed = [
        item for item in candidates
        if item.get("candidate_type") == "inflection_auto_override"
        and item.get("status") != "auto_approved_verified"
    ]
    needs_review = [item for item in candidates if item.get("status") == "needs_human_review"]
    observed = [item for item in candidates if item.get("status") == "observed"]

    lines = [
        "ОТЧЕТ АГЕНТА",
        "",
        "Сводка:",
        f"- Склонений проверено: {len(inflection_rows)}",
        f"- Автоматически утверждено и проверено: {len(auto_verified)}",
        f"- Автоутверждений с ошибкой/отклонением: {len(auto_failed)}",
        f"- В карантине: {len(quarantine)}",
        f"- Кандидатов на ручную проверку: {len(needs_review)}",
        f"- Безопасных правок филолога зафиксировано: {len(observed)}",
        "",
        "Филолог:",
        f"- Статус: {philologist_state.get('status', 'idle')}",
        f"- Проверено документов: {philologist_state.get('processed_documents', 0)}",
        f"- Документов с замечаниями: {philologist_state.get('documents_with_issues', 0)}",
        f"- Документов с автоправками: {philologist_state.get('fixed_documents', 0)}",
        "",
    ]
    plan = philologist_state.get("plan") or {}
    if isinstance(plan, dict) and plan:
        execution = plan.get("execution") or {}
        lines.extend(
            [
                "План агента:",
                f"- Статус плана: {plan.get('status', 'unknown')}",
                f"- Цикл исполнения: {execution.get('status', 'not_started') if isinstance(execution, dict) else 'not_started'}",
                f"- Цель: {plan.get('goal', '')}",
            ]
        )
        for step in (plan.get("steps") or [])[:8]:
            lines.append(
                "- "
                f"{step.get('id')}: {step.get('status')} "
                f"({step.get('tool')}) - {step.get('reason')}"
            )
        lines.append("")

    lines.extend(_format_report_section("Автоматически принято", auto_verified, limit=8))
    lines.extend(_format_report_section("Карантин", quarantine, limit=8))
    lines.extend(_format_report_section("Нужно проверить человеку", needs_review, limit=8))
    lines.extend(_format_report_section("Ошибки автоутверждения", auto_failed, limit=8))
    return "\n".join(lines).strip() + "\n"


def save_agent_report(
    job_id: str | None = None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    quarantine: list[dict[str, Any]] | None = None,
) -> Path:
    report_path = get_agent_report_path(job_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        build_agent_report(job_id, candidates=candidates, quarantine=quarantine),
        encoding="utf-8",
    )
    return report_path


def build_quarantine_items(job_id: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in load_inflection_log(job_id):
        decision = _safe_auto_approval_decision(row)
        if decision["allowed"]:
            continue
        items.append(_quarantine_record(row, reason=decision["reason"]))

    for candidate in _build_philologist_candidates(job_id):
        if candidate.get("status") == "needs_human_review":
            items.append(
                {
                    "quarantine_type": "philology_review",
                    "status": "quarantine",
                    "source": candidate.get("source", ""),
                    "row_id": candidate.get("row_id", ""),
                    "document": candidate.get("document", ""),
                    "location": candidate.get("location", ""),
                    "field": "",
                    "source_value": "",
                    "result_value": candidate.get("suggestion", ""),
                    "method": "",
                    "confidence": "",
                    "reason": candidate.get("reason", ""),
                    "warning": candidate.get("issue", ""),
                    "next_action": "Проверить правку человеком или через RAG/LLM перед добавлением в правило.",
                }
            )
    return items


def save_quarantine(job_id: str | None, items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    items = build_quarantine_items(job_id) if items is None else items
    path = get_agent_quarantine_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return items


def save_quarantine_csv(items: list[dict[str, Any]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "quarantine_type",
        "status",
        "source",
        "row_id",
        "document",
        "location",
        "field",
        "source_value",
        "result_value",
        "method",
        "confidence",
        "reason",
        "warning",
        "next_action",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({field: item.get(field, "") for field in fieldnames})


def _format_report_section(title: str, items: list[dict[str, Any]], *, limit: int) -> list[str]:
    lines = [f"{title}:"]
    if not items:
        lines.append("- нет")
        lines.append("")
        return lines
    for item in items[:limit]:
        field = item.get("field") or item.get("document") or "item"
        source_value = item.get("source_value") or item.get("warning") or ""
        result_value = item.get("result_value") or item.get("suggestion") or ""
        reason = item.get("reason") or item.get("next_action") or ""
        line = f"- {field}: {source_value} -> {result_value}"
        if reason:
            line += f" ({reason})"
        lines.append(line)
    if len(items) > limit:
        lines.append(f"- ...и ещё {len(items) - limit}")
    lines.append("")
    return lines


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


def _quarantine_record(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    warning = str(row.get("warning") or "").strip()
    next_action = "Проверить форму и при подтверждении добавить override."
    if reason == "has_warning":
        next_action = "Разобрать warning и подтвердить правильную форму вручную."
    elif reason == "method_not_trusted_for_auto_approval":
        next_action = "Проверить источник решения; fallback/override не автоутверждаются."
    elif reason == "empty_or_unchanged_value":
        next_action = "Проверить, почему форма не изменилась или отсутствует."

    return {
        "quarantine_type": "inflection_review",
        "status": "quarantine",
        "source": "case_engine",
        "row_id": row.get("row_id", ""),
        "document": "",
        "location": "",
        "field": row.get("field", ""),
        "source_value": row.get("source_value", ""),
        "result_value": row.get("result_value", ""),
        "method": row.get("method", ""),
        "confidence": row.get("confidence", ""),
        "reason": reason,
        "warning": warning,
        "next_action": next_action,
    }


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
