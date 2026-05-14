from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from src.generator.orchestration.agent_handoff import (
    append_agent_event,
    get_tasks_for_agent,
    get_stale_tasks,
    set_task_statuses,
)
from src.generator.generation.generator_agent import run_generator_agent
from src.generator.philologist.philologist_agent import run_philologist
from src.generator.delivery.sender_agent import run_sender
from src.utils.config import settings
from src.utils.logger import logger


AUTONOMOUS_WORKER_STATE: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "last_cycle_at": None,
    "last_error": "",
    "generator_runs": 0,
    "philologist_runs": 0,
    "sender_rechecks": 0,
    "escalated_tasks": 0,
}


class AutonomousWorker:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if not settings.autonomous_workers_enabled:
            AUTONOMOUS_WORKER_STATE["status"] = "disabled"
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="autonomous-agent-worker",
            daemon=True,
        )
        AUTONOMOUS_WORKER_STATE.update(
            {
                "status": "running",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "last_error": "",
            }
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        AUTONOMOUS_WORKER_STATE["status"] = "stopped"

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
                AUTONOMOUS_WORKER_STATE["last_error"] = ""
            except Exception as exc:  # pragma: no cover
                AUTONOMOUS_WORKER_STATE["last_error"] = str(exc)
                append_agent_event(
                    source_agent="autonomous_worker",
                    target_agent="orchestrator",
                    event_type="worker_error",
                    message=f"Фоновый worker столкнулся с ошибкой: {exc}",
                    details={},
                )
                logger.exception("autonomous_worker_cycle_failed")
            AUTONOMOUS_WORKER_STATE["last_cycle_at"] = datetime.now().isoformat(timespec="seconds")
            self._stop_event.wait(max(1, settings.autonomous_workers_poll_seconds))

    def _run_cycle(self) -> None:
        self._escalate_stale_tasks()
        self._process_generator_tasks()
        self._process_philologist_tasks()
        self._process_sender_resume_tasks()

    def _escalate_stale_tasks(self) -> None:
        timeout_seconds = max(30, int(settings.autonomous_task_timeout_seconds))
        for agent_name in ("generator", "philologist", "sender"):
            stale_tasks = get_stale_tasks(agent_name, older_than_seconds=timeout_seconds)
            for task in stale_tasks:
                row_id = task.get("row_id")
                task_type = str(task.get("task_type")).strip() or None
                touched = set_task_statuses(
                    agent_name,
                    row_id=row_id,
                    task_type=task_type,
                    new_status="escalated",
                    note=(
                        f"Задача зависла дольше {timeout_seconds} сек. "
                        "Нужна проверка оркестратором или человеком."
                    ),
                    resolution_summary="Автономный worker не дождался решения задачи в SLA.",
                    only_statuses=("pending", "in_progress"),
                )
                if touched:
                    AUTONOMOUS_WORKER_STATE["escalated_tasks"] += len(touched)
                    append_agent_event(
                        source_agent="autonomous_worker",
                        target_agent="orchestrator",
                        event_type="task_escalated",
                        message=(
                            f"Фоновый worker эскалировал задачу {task_type or 'unknown'} "
                            f"для агента {agent_name} по строке {row_id}."
                        ),
                        row_id=row_id,
                        mun_name=str(task.get("mun_name") or ""),
                        task_id=str(task.get("id") or ""),
                        details={
                            "timeout_seconds": timeout_seconds,
                            "agent_name": agent_name,
                        },
                    )

    def _process_generator_tasks(self) -> None:
        pending_rows = sorted({
            str(task.get("row_id")).strip()
            for task in get_tasks_for_agent("generator")
            if str(task.get("status")).strip() == "pending"
            and str(task.get("row_id")).strip()
        })
        if not pending_rows:
            return
        append_agent_event(
            source_agent="autonomous_worker",
            target_agent="generator",
            event_type="worker_dispatch",
            message=f"Фоновый worker передал генератору {len(pending_rows)} строк на восстановление.",
            details={"row_ids": pending_rows},
        )
        run_generator_agent(row_ids=pending_rows, limit=len(pending_rows))
        AUTONOMOUS_WORKER_STATE["generator_runs"] += 1

    def _process_philologist_tasks(self) -> None:
        if not settings.philologist_auto_run_enabled:
            return
        pending_rows = sorted({
            str(task.get("row_id")).strip()
            for task in get_tasks_for_agent("philologist")
            if str(task.get("status")).strip() == "pending"
            and str(task.get("row_id")).strip()
        })
        if not pending_rows:
            return
        append_agent_event(
            source_agent="autonomous_worker",
            target_agent="philologist",
            event_type="worker_dispatch",
            message=f"Фоновый worker передал филологу {len(pending_rows)} строк на проверку.",
            details={"row_ids": pending_rows},
        )
        run_philologist(ai_enabled=True, row_ids=pending_rows)
        AUTONOMOUS_WORKER_STATE["philologist_runs"] += 1

    def _process_sender_resume_tasks(self) -> None:
        pending_tasks = [
            task for task in get_tasks_for_agent("sender")
            if str(task.get("status")).strip() == "pending"
            and str(task.get("task_type")).strip() == "resume_send_readiness"
            and str(task.get("row_id")).strip()
        ]
        if not pending_tasks:
            return

        row_ids = sorted({str(task.get("row_id")).strip() for task in pending_tasks})
        for row_id in row_ids:
            set_task_statuses(
                "sender",
                row_id=row_id,
                task_type="resume_send_readiness",
                new_status="in_progress",
                note="Отправщик повторно проверяет строку после исправления другим агентом.",
                only_statuses=("pending",),
            )

        append_agent_event(
            source_agent="autonomous_worker",
            target_agent="sender",
            event_type="worker_dispatch",
            message=f"Фоновый worker попросил отправщика повторно проверить {len(row_ids)} строк после исправления.",
            details={"row_ids": row_ids},
        )
        result = run_sender(dry_run=True, auto_recover=False, row_ids=row_ids)
        row_results = {str(item.get("id")).strip(): item for item in (result.get("rows") or [])}

        for row_id in row_ids:
            row_result = row_results.get(row_id) or {}
            resolved = str(row_result.get("result")).strip() in {"ready", "ready_after_recovery"}
            set_task_statuses(
                "sender",
                row_id=row_id,
                task_type="resume_send_readiness",
                new_status="done" if resolved else "blocked",
                note=(
                    "Отправщик перепроверил строку: теперь она снова готова к отправке."
                    if resolved else
                    f"Отправщик перепроверил строку, но проблема ещё осталась: {row_result.get('error') or row_result.get('result') or 'неизвестно'}."
                ),
                resolution_summary=(
                    "Строка снова готова к отправке."
                    if resolved else
                    "После перепроверки строка пока не готова к отправке."
                ),
                only_statuses=("in_progress", "pending"),
            )
        AUTONOMOUS_WORKER_STATE["sender_rechecks"] += 1


WORKER = AutonomousWorker()


def start_autonomous_worker() -> None:
    WORKER.start()


def stop_autonomous_worker() -> None:
    WORKER.stop()


def get_autonomous_worker_state() -> dict[str, Any]:
    return dict(AUTONOMOUS_WORKER_STATE)
