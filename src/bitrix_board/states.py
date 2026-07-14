from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    PLANNING = "planning"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    READY_TO_RESUME = "ready_to_resume"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    REWORKING = "reworking"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


TERMINAL_STATES = frozenset({TaskState.COMPLETED, TaskState.BLOCKED, TaskState.FAILED})

SLOT_FREE_STATES = frozenset(
    {
        TaskState.QUEUED,
        TaskState.WAITING_FOR_ANSWER,
        TaskState.READY_TO_RESUME,
        TaskState.COMPLETED,
        TaskState.BLOCKED,
        TaskState.FAILED,
    }
)

ACTIVE_SLOT_STATES = frozenset(
    {
        TaskState.PREPARING,
        TaskState.PLANNING,
        TaskState.IMPLEMENTING,
        TaskState.TESTING,
        TaskState.REVIEWING,
        TaskState.REWORKING,
    }
)
