from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.generator.agent_memory import build_quarantine_items
from src.generator.config_generator import OUTPUT_DIR
from src.generator.inflection_report import load_inflection_log
from src.jobs import resolve_job_paths, resolve_state_path


def _load_state(agent_name: str, job_id: str | None = None) -> dict[str, Any]:
    path = resolve_state_path(agent_name, job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _count_docx(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0
    return sum(1 for _ in output_dir.rglob("*.docx"))


def _step(
    step_id: str,
    *,
    tool: str,
    status: str,
    reason: str,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "tool": tool,
        "status": status,
        "reason": reason,
        "depends_on": depends_on or [],
    }


def build_philologist_plan(
    job_id: str | None = None,
    *,
    output_dir: Path | None = None,
    current_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a visible agent plan for the philologist workflow.

    This is intentionally lightweight: it does not execute tools by itself yet,
    but it gives the agent an explicit planning layer instead of hiding all
    decisions inside procedural code.
    """

    job_paths = resolve_job_paths(job_id)
    target_dir = output_dir or (job_paths.output_dir if not job_paths.uses_legacy_layout else OUTPUT_DIR)
    philologist_state = current_state if current_state is not None else _load_state("philologist", job_id)
    inflection_rows = load_inflection_log(job_id)
    quarantine = build_quarantine_items(job_id)
    docx_count = _count_docx(target_dir)
    completed_documents = int(philologist_state.get("processed_documents") or 0)
    fixed_documents = int(philologist_state.get("fixed_documents") or 0)
    documents_with_issues = int(philologist_state.get("documents_with_issues") or 0)
    status = str(philologist_state.get("status") or "idle")

    inflection_warning_count = sum(
        1
        for row in inflection_rows
        if str(row.get("warning") or "").strip()
    )
    observations: list[str] = []
    if docx_count == 0:
        observations.append("Готовые DOCX не найдены: филологу пока нечего проверять.")
    else:
        observations.append(f"Найдено DOCX для проверки: {docx_count}.")
    if inflection_rows:
        observations.append(
            f"Журнал склонений доступен: {len(inflection_rows)} записей, предупреждений: {inflection_warning_count}."
        )
    else:
        observations.append("Журнал склонений пока не найден или пуст.")
    if quarantine:
        observations.append(f"В карантине агента есть решения для ручной проверки: {len(quarantine)}.")
    if status == "completed":
        observations.append("Предыдущий запуск филолога завершен.")

    if docx_count == 0:
        plan_status = "blocked"
    elif status == "running":
        plan_status = "running"
    elif status == "completed" and completed_documents >= docx_count:
        plan_status = "completed" if not quarantine else "needs_review"
    else:
        plan_status = "ready"

    steps = [
        _step(
            "inspect_job",
            tool="inspect_job",
            status="done",
            reason="Собраны входные факты: папка output, состояние филолога, журнал склонений и карантин.",
        ),
        _step(
            "read_inflection_log",
            tool="read_inflection_log",
            status="done" if inflection_rows else "skipped",
            reason=(
                "Журнал склонений будет использован для поиска рискованных падежей."
                if inflection_rows
                else "Журнала склонений нет, проверка продолжится только по тексту документов."
            ),
            depends_on=["inspect_job"],
        ),
        _step(
            "review_docx",
            tool="review_docx",
            status="blocked" if docx_count == 0 else ("done" if status == "completed" else "pending"),
            reason=(
                "Нет DOCX-файлов для проверки."
                if docx_count == 0
                else "Нужно проверить текст документов локальными правилами и LLM."
            ),
            depends_on=["inspect_job", "read_inflection_log"],
        ),
        _step(
            "apply_safe_fixes",
            tool="apply_safe_fixes",
            status="done" if status == "completed" else ("blocked" if docx_count == 0 else "conditional"),
            reason="Применяются только безопасные точечные правки, которые не ломают стили.",
            depends_on=["review_docx"],
        ),
        _step(
            "rebuild_pdf",
            tool="rebuild_pdf",
            status="done" if fixed_documents > 0 else ("skipped" if status == "completed" else "conditional"),
            reason="PDF пересобирается только для документов, где были внесены правки.",
            depends_on=["apply_safe_fixes"],
        ),
        _step(
            "update_memory",
            tool="save_learning_memory",
            status="done" if status == "completed" else ("blocked" if docx_count == 0 else "pending"),
            reason="Безопасные решения уходят в память, рискованные - в карантин.",
            depends_on=["apply_safe_fixes"],
        ),
        _step(
            "finalize_report",
            tool="save_agent_report",
            status="done" if status == "completed" else ("blocked" if docx_count == 0 else "pending"),
            reason="Формируется понятный отчет: что исправлено, что принято автоматически, что нужно проверить.",
            depends_on=["update_memory"],
        ),
    ]

    return {
        "goal": "Проверить готовые документы, безопасно исправить языковые ошибки и зафиксировать спорные решения.",
        "status": plan_status,
        "job_id": job_paths.job_id,
        "output_dir": str(target_dir),
        "observations": observations,
        "counts": {
            "docx": docx_count,
            "processed_documents": completed_documents,
            "fixed_documents": fixed_documents,
            "documents_with_issues": documents_with_issues,
            "inflection_log_rows": len(inflection_rows),
            "inflection_warning_rows": inflection_warning_count,
            "quarantine_items": len(quarantine),
        },
        "steps": steps,
    }
