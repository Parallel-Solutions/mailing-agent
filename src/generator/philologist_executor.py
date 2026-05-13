from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


class PhilologistAgentLoop:
    """Small execution loop that makes the philologist workflow observable.

    The existing philologist code still owns the actual DOCX processing. This
    loop records the agent-style cycle around it: plan, observe, execute step,
    update state. That gives us a safe bridge toward a more autonomous agent
    without breaking the current production path.
    """

    def __init__(self, plan: dict[str, Any], *, event_limit: int = 250) -> None:
        self.plan = deepcopy(plan)
        self.event_limit = event_limit
        execution = self.plan.setdefault("execution", {})
        execution.setdefault("status", "created")
        execution.setdefault("started_at", None)
        execution.setdefault("completed_at", None)
        execution.setdefault("events", [])

    def start(self, observation: str = "План принят к исполнению.") -> None:
        execution = self.plan["execution"]
        execution["status"] = "running"
        execution["started_at"] = execution.get("started_at") or _now()
        self.observe("loop_start", observation)

    def observe(
        self,
        event_type: str,
        message: str,
        *,
        step_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        events = self.plan["execution"].setdefault("events", [])
        events.append(
            {
                "ts": _now(),
                "type": event_type,
                "step_id": step_id,
                "message": message,
                "data": data or {},
            }
        )
        if len(events) > self.event_limit:
            del events[: len(events) - self.event_limit]

    def mark_step(
        self,
        step_id: str,
        status: str,
        observation: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        for step in self.plan.get("steps", []):
            if step.get("id") != step_id:
                continue
            step["status"] = status
            step["last_observation"] = observation
            step["updated_at"] = _now()
            if data:
                step["metrics"] = data
            break
        self.observe("step_update", observation, step_id=step_id, data={"status": status, **(data or {})})

    def next_step(self) -> dict[str, Any] | None:
        for step in self.plan.get("steps", []):
            if step.get("status") in {"pending", "conditional"}:
                return step
        return None

    def complete(self, status: str, observation: str) -> None:
        execution = self.plan["execution"]
        execution["status"] = status
        execution["completed_at"] = _now()
        self.plan["status"] = status
        self.observe("loop_complete", observation)

    def as_plan(self) -> dict[str, Any]:
        next_step = self.next_step()
        self.plan["next_step"] = next_step.get("id") if next_step else None
        return deepcopy(self.plan)


def merge_plan_execution(fresh_plan: dict[str, Any], previous_plan: dict[str, Any] | None) -> dict[str, Any]:
    if not previous_plan:
        return deepcopy(fresh_plan)
    merged = deepcopy(fresh_plan)
    execution = previous_plan.get("execution")
    if execution:
        merged["execution"] = deepcopy(execution)
    previous_steps = {
        str(step.get("id")): step
        for step in previous_plan.get("steps", [])
        if step.get("id")
    }
    for step in merged.get("steps", []):
        previous_step = previous_steps.get(str(step.get("id")))
        if not previous_step:
            continue
        if previous_step.get("status") in {"done", "skipped", "blocked", "error"}:
            step["status"] = previous_step.get("status")
        for key in ("last_observation", "updated_at", "metrics"):
            if key in previous_step:
                step[key] = deepcopy(previous_step[key])
    if previous_plan.get("next_step"):
        merged["next_step"] = previous_plan.get("next_step")
    return merged


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
