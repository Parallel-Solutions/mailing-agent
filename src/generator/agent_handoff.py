from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.generator.config_generator import DATA_DIR
from src.jobs.storage import resolve_job_paths
from src.utils.config import settings


TASKS_PATH = DATA_DIR / "agent_tasks.json"
EVENTS_PATH = DATA_DIR / "agent_events.json"


def _resolve_handoff_paths(job_id: str | None = None) -> tuple[Path, Path]:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return TASKS_PATH, EVENTS_PATH
    state_dir = job_paths.root_dir / "state"
    return state_dir / "agent_tasks.json", state_dir / "agent_events.json"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_retry_key(*, target_agent: str, task_type: str, row_id: Any) -> str:
    return f"{_safe_text(target_agent)}::{_safe_text(task_type)}::{_safe_text(row_id)}"


def load_tasks(job_id: str | None = None) -> list[dict[str, Any]]:
    tasks_path, _ = _resolve_handoff_paths(job_id)
    if not tasks_path.exists():
        return []
    try:
        payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def load_events(job_id: str | None = None) -> list[dict[str, Any]]:
    _, events_path = _resolve_handoff_paths(job_id)
    if not events_path.exists():
        return []
    try:
        payload = json.loads(events_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_tasks(tasks: list[dict[str, Any]], job_id: str | None = None) -> None:
    tasks_path, _ = _resolve_handoff_paths(job_id)
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_events(events: list[dict[str, Any]], job_id: str | None = None) -> None:
    _, events_path = _resolve_handoff_paths(job_id)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(events[-1000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_agent_event(
    *,
    source_agent: str,
    target_agent: str | None,
    event_type: str,
    message: str,
    row_id: Any = None,
    mun_name: str = "",
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    events = load_events(job_id)
    event = {
        "id": uuid4().hex[:12],
        "source_agent": source_agent,
        "target_agent": target_agent or "",
        "event_type": event_type,
        "message": _safe_text(message),
        "task_id": task_id or "",
        "row_id": row_id,
        "mun_name": mun_name,
        "details": details or {},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    events.append(event)
    save_events(events, job_id)
    return event


def get_recent_events(*, agent_name: str | None = None, limit: int = 50, job_id: str | None = None) -> list[dict[str, Any]]:
    events = load_events(job_id)
    if agent_name:
        agent_name = _safe_text(agent_name)
        events = [
            item
            for item in events
            if _safe_text(item.get("source_agent")) == agent_name
            or _safe_text(item.get("target_agent")) == agent_name
        ]
    return events[-limit:]


def create_task(
    *,
    source_agent: str,
    target_agent: str,
    task_type: str,
    row_id: Any,
    mun_name: str,
    details: dict[str, Any] | None = None,
    owner_agent: str | None = None,
    problem_type: str | None = None,
    symptom: str = "",
    root_cause: str = "",
    priority: str = "medium",
    blocking: bool = False,
    can_retry_after: bool = False,
    job_id: str | None = None,
) -> dict[str, Any]:
    tasks = load_tasks(job_id)
    row_id_text = _safe_text(row_id)
    retry_key = _build_retry_key(target_agent=target_agent, task_type=task_type, row_id=row_id)

    for item in tasks:
        if (
            _safe_text(item.get("source_agent")) == source_agent
            and _safe_text(item.get("target_agent")) == target_agent
            and _safe_text(item.get("task_type")) == task_type
            and _safe_text(item.get("row_id")) == row_id_text
            and _safe_text(item.get("status")) in {"pending", "in_progress"}
        ):
            return item

    retry_count = sum(1 for item in tasks if _safe_text(item.get("retry_key")) == retry_key)
    max_retries = max(1, int(settings.autonomous_task_max_retries))
    should_escalate = retry_count >= max_retries

    task = {
        "id": uuid4().hex[:12],
        "source_agent": source_agent,
        "owner_agent": owner_agent or target_agent,
        "target_agent": target_agent,
        "task_type": task_type,
        "problem_type": problem_type or task_type,
        "symptom": _safe_text(symptom),
        "root_cause": _safe_text(root_cause),
        "row_id": row_id,
        "mun_name": mun_name,
        "details": details or {},
        "status": "escalated" if should_escalate else "pending",
        "priority": _safe_text(priority) or "medium",
        "blocking": bool(blocking),
        "can_retry_after": bool(can_retry_after),
        "resolution_summary": (
            f"Превышен лимит автоповторов ({max_retries}) для этого кейса."
            if should_escalate else ""
        ),
        "retry_key": retry_key,
        "retry_count": retry_count,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    tasks.append(task)
    save_tasks(tasks, job_id)
    append_agent_event(
        source_agent=source_agent,
        target_agent=target_agent,
        event_type="task_escalated" if should_escalate else "task_created",
        message=(
            (
                f"{source_agent} эскалировал задачу {task_type} для {target_agent}, "
                f"потому что превышен лимит автоповторов ({max_retries}). "
            )
            if should_escalate else
            (
                f"{source_agent} создал задачу {task_type} для {target_agent}. "
            )
        ) + (
            f"Симптом: {_safe_text(symptom) or task_type}. "
            f"Причина: {_safe_text(root_cause) or 'не указана'}"
        ),
        row_id=row_id,
        mun_name=mun_name,
        task_id=task["id"],
        details={
            **(details or {}),
            "problem_type": task["problem_type"],
            "priority": task["priority"],
            "blocking": task["blocking"],
            "retry_count": retry_count,
            "retry_key": retry_key,
        },
        job_id=job_id,
    )
    return task


def get_tasks_for_agent(agent_name: str, job_id: str | None = None) -> list[dict[str, Any]]:
    return [item for item in load_tasks(job_id) if _safe_text(item.get("target_agent")) == agent_name]


def count_tasks_for_agent(agent_name: str, job_id: str | None = None) -> dict[str, int]:
    result = {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0, "escalated": 0}
    for item in get_tasks_for_agent(agent_name, job_id):
        result["total"] += 1
        status = _safe_text(item.get("status")) or "pending"
        if status in result:
            result[status] += 1
    return result


def mark_tasks_in_progress(
    agent_name: str,
    limit: int | None = None,
    row_ids: list[str] | None = None,
    job_id: str | None = None,
) -> list[dict[str, Any]]:
    tasks = load_tasks(job_id)
    touched: list[dict[str, Any]] = []
    remaining = limit
    requested_row_ids = {_safe_text(item) for item in (row_ids or []) if _safe_text(item)}
    for item in tasks:
        if _safe_text(item.get("target_agent")) != agent_name:
            continue
        if _safe_text(item.get("status")) != "pending":
            continue
        if requested_row_ids and _safe_text(item.get("row_id")) not in requested_row_ids:
            continue
        item["status"] = "in_progress"
        item["updated_at"] = datetime.now().isoformat(timespec="seconds")
        touched.append(item)
        append_agent_event(
            source_agent=agent_name,
            target_agent=_safe_text(item.get("source_agent")) or None,
            event_type="task_claimed",
            message=f"{agent_name} принял задачу {item.get('task_type')} в работу.",
            row_id=item.get("row_id"),
            mun_name=_safe_text(item.get("mun_name")),
            task_id=_safe_text(item.get("id")) or None,
            details={"status": "in_progress"},
            job_id=job_id,
        )
        if remaining is not None:
            remaining -= 1
            if remaining <= 0:
                break
    if touched:
        save_tasks(tasks, job_id)
    return touched


def set_task_statuses(
    target_agent: str,
    *,
    row_id: Any = None,
    task_type: str | None = None,
    new_status: str,
    note: str = "",
    resolution_summary: str = "",
    only_statuses: tuple[str, ...] = ("pending", "in_progress"),
    job_id: str | None = None,
) -> list[dict[str, Any]]:
    tasks = load_tasks(job_id)
    touched: list[dict[str, Any]] = []
    row_id_text = _safe_text(row_id)
    event_type = {
        "done": "task_done",
        "blocked": "task_blocked",
        "escalated": "task_escalated",
        "pending": "task_reopened",
    }.get(new_status, "task_updated")

    for item in tasks:
        if _safe_text(item.get("target_agent")) != _safe_text(target_agent):
            continue
        if task_type and _safe_text(item.get("task_type")) != _safe_text(task_type):
            continue
        if row_id_text and _safe_text(item.get("row_id")) != row_id_text:
            continue
        if only_statuses and _safe_text(item.get("status")) not in set(only_statuses):
            continue
        item["status"] = new_status
        item["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if resolution_summary:
            item["resolution_summary"] = resolution_summary
        if note:
            details = item.setdefault("details", {})
            if isinstance(details, dict):
                details["note"] = note
        touched.append(item)
        append_agent_event(
            source_agent=target_agent,
            target_agent=_safe_text(item.get("source_agent")) or None,
            event_type=event_type,
            message=note or f"{target_agent} обновил задачу {item.get('task_type')} -> {new_status}.",
            row_id=item.get("row_id"),
            mun_name=_safe_text(item.get("mun_name")),
            task_id=_safe_text(item.get("id")) or None,
            details={
                "status": new_status,
                "resolution_summary": resolution_summary,
            },
            job_id=job_id,
        )

    if touched:
        save_tasks(tasks, job_id)
    return touched


def get_stale_tasks(
    agent_name: str,
    *,
    older_than_seconds: int,
    statuses: tuple[str, ...] = ("pending", "in_progress"),
    job_id: str | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now()
    stale: list[dict[str, Any]] = []
    for item in get_tasks_for_agent(agent_name, job_id):
        status = _safe_text(item.get("status"))
        if status not in set(statuses):
            continue
        updated_at_text = _safe_text(item.get("updated_at")) or _safe_text(item.get("created_at"))
        if not updated_at_text:
            continue
        try:
            updated_at = datetime.fromisoformat(updated_at_text)
        except ValueError:
            continue
        if (now - updated_at).total_seconds() >= older_than_seconds:
            stale.append(item)
    return stale
