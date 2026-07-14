from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.bitrix_board.states import ACTIVE_SLOT_STATES, SLOT_FREE_STATES, TERMINAL_STATES, TaskState

SCHEMA_VERSION = 1


@dataclass
class BoardRun:
    id: int
    project: str
    source_stage: str
    target_stage: str
    concurrency: int
    snapshot_task_ids: list[int]
    stop_requested: bool
    dispatcher_pid: int | None
    created_at: float
    updated_at: float


@dataclass
class BoardTask:
    id: int
    run_id: int
    task_id: int
    title: str
    state: TaskState
    position: int
    worktree_path: str | None
    branch: str | None
    plan_json: dict[str, Any] | None
    questions_json: dict[str, Any] | None
    clarifications_json: list[dict[str, Any]] | None
    last_chat_message_id: str | None
    last_question_hash: str | None
    review_cycle: int
    agent_pid: int | None
    log_path: str | None
    state_path: str | None
    error_message: str | None
    report_json: dict[str, Any] | None
    created_at: float
    updated_at: float


class BoardStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                source_stage TEXT NOT NULL,
                target_stage TEXT NOT NULL,
                concurrency INTEGER NOT NULL,
                snapshot_task_ids TEXT NOT NULL,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                dispatcher_pid INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS board_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                position INTEGER NOT NULL,
                worktree_path TEXT,
                branch TEXT,
                plan_json TEXT,
                questions_json TEXT,
                clarifications_json TEXT,
                last_chat_message_id TEXT,
                last_question_hash TEXT,
                review_cycle INTEGER NOT NULL DEFAULT 0,
                agent_pid INTEGER,
                log_path TEXT,
                state_path TEXT,
                error_message TEXT,
                report_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(run_id, task_id),
                FOREIGN KEY(run_id) REFERENCES board_runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_board_tasks_run_state
                ON board_tasks(run_id, state);
            """
        )
        self._conn.commit()

    @staticmethod
    def _loads_json(raw: str | None) -> Any:
        if not raw:
            return None
        return json.loads(raw)

    @staticmethod
    def _dumps_json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def _row_to_run(self, row: sqlite3.Row) -> BoardRun:
        return BoardRun(
            id=row["id"],
            project=row["project"],
            source_stage=row["source_stage"],
            target_stage=row["target_stage"],
            concurrency=row["concurrency"],
            snapshot_task_ids=json.loads(row["snapshot_task_ids"]),
            stop_requested=bool(row["stop_requested"]),
            dispatcher_pid=row["dispatcher_pid"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_task(self, row: sqlite3.Row) -> BoardTask:
        return BoardTask(
            id=row["id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            title=row["title"],
            state=TaskState(row["state"]),
            position=row["position"],
            worktree_path=row["worktree_path"],
            branch=row["branch"],
            plan_json=self._loads_json(row["plan_json"]),
            questions_json=self._loads_json(row["questions_json"]),
            clarifications_json=self._loads_json(row["clarifications_json"]),
            last_chat_message_id=row["last_chat_message_id"],
            last_question_hash=row["last_question_hash"],
            review_cycle=row["review_cycle"],
            agent_pid=row["agent_pid"],
            log_path=row["log_path"],
            state_path=row["state_path"],
            error_message=row["error_message"],
            report_json=self._loads_json(row["report_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_run(
        self,
        *,
        project: str,
        source_stage: str,
        target_stage: str,
        concurrency: int,
        snapshot_task_ids: list[int],
        task_titles: dict[int, str],
    ) -> BoardRun:
        now = time.time()
        cursor = self._conn.execute(
            """
            INSERT INTO board_runs (
                project, source_stage, target_stage, concurrency,
                snapshot_task_ids, stop_requested, dispatcher_pid,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
            """,
            (
                project,
                source_stage,
                target_stage,
                concurrency,
                json.dumps(snapshot_task_ids),
                now,
                now,
            ),
        )
        run_id = int(cursor.lastrowid)
        for position, task_id in enumerate(snapshot_task_ids):
            self._conn.execute(
                """
                INSERT INTO board_tasks (
                    run_id, task_id, title, state, position,
                    review_cycle, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    task_titles.get(task_id, f"Task {task_id}"),
                    TaskState.QUEUED.value,
                    position,
                    now,
                    now,
                ),
            )
        self._conn.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: int) -> BoardRun:
        row = self._conn.execute("SELECT * FROM board_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Run {run_id} not found")
        return self._row_to_run(row)

    def get_latest_run(self) -> BoardRun | None:
        row = self._conn.execute(
            "SELECT * FROM board_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self._row_to_run(row) if row else None

    def get_active_run(self) -> BoardRun | None:
        row = self._conn.execute(
            """
            SELECT * FROM board_runs
            WHERE stop_requested = 0
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        run = self._row_to_run(row)
        tasks = self.list_tasks(run.id)
        if all(task.state in TERMINAL_STATES for task in tasks):
            return None
        return run

    def set_dispatcher_pid(self, run_id: int, pid: int | None) -> None:
        now = time.time()
        self._conn.execute(
            "UPDATE board_runs SET dispatcher_pid = ?, updated_at = ? WHERE id = ?",
            (pid, now, run_id),
        )
        self._conn.commit()

    def request_stop(self, run_id: int) -> None:
        now = time.time()
        self._conn.execute(
            "UPDATE board_runs SET stop_requested = 1, updated_at = ? WHERE id = ?",
            (now, run_id),
        )
        self._conn.commit()

    def clear_stop(self, run_id: int) -> None:
        now = time.time()
        self._conn.execute(
            "UPDATE board_runs SET stop_requested = 0, updated_at = ? WHERE id = ?",
            (now, run_id),
        )
        self._conn.commit()

    def list_tasks(self, run_id: int) -> list[BoardTask]:
        rows = self._conn.execute(
            "SELECT * FROM board_tasks WHERE run_id = ? ORDER BY position ASC, task_id ASC",
            (run_id,),
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_task_by_bitrix_id(self, run_id: int, task_id: int) -> BoardTask | None:
        row = self._conn.execute(
            "SELECT * FROM board_tasks WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        return self._row_to_task(row) if row else None

    def update_task(self, task_db_id: int, **fields: Any) -> BoardTask:
        allowed = {
            "title",
            "state",
            "worktree_path",
            "branch",
            "plan_json",
            "questions_json",
            "clarifications_json",
            "last_chat_message_id",
            "last_question_hash",
            "review_cycle",
            "agent_pid",
            "log_path",
            "state_path",
            "error_message",
            "report_json",
        }
        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key.endswith("_json"):
                value = self._dumps_json(value)
            elif key == "state" and isinstance(value, TaskState):
                value = value.value
            updates.append(f"{key} = ?")
            values.append(value)
        updates.append("updated_at = ?")
        values.append(time.time())
        values.append(task_db_id)
        self._conn.execute(
            f"UPDATE board_tasks SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM board_tasks WHERE id = ?", (task_db_id,)).fetchone()
        if row is None:
            raise KeyError(f"Task {task_db_id} not found")
        return self._row_to_task(row)

    def count_active_slots(self, run_id: int) -> int:
        tasks = self.list_tasks(run_id)
        return sum(1 for task in tasks if task.state in ACTIVE_SLOT_STATES)

    def next_queued_task(self, run_id: int) -> BoardTask | None:
        row = self._conn.execute(
            """
            SELECT * FROM board_tasks
            WHERE run_id = ? AND state = ?
            ORDER BY position ASC, task_id ASC
            LIMIT 1
            """,
            (run_id, TaskState.QUEUED.value),
        ).fetchone()
        return self._row_to_task(row) if row else None

    def next_ready_to_resume_task(self, run_id: int) -> BoardTask | None:
        row = self._conn.execute(
            """
            SELECT * FROM board_tasks
            WHERE run_id = ? AND state = ?
            ORDER BY position ASC, task_id ASC
            LIMIT 1
            """,
            (run_id, TaskState.READY_TO_RESUME.value),
        ).fetchone()
        return self._row_to_task(row) if row else None

    def tasks_by_state(self, run_id: int, state: TaskState) -> list[BoardTask]:
        rows = self._conn.execute(
            """
            SELECT * FROM board_tasks
            WHERE run_id = ? AND state = ?
            ORDER BY position ASC, task_id ASC
            """,
            (run_id, state.value),
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def status_summary(self, run_id: int) -> dict[str, list[BoardTask]]:
        tasks = self.list_tasks(run_id)
        summary: dict[str, list[BoardTask]] = {
            "active_slots": [],
            "waiting_for_answer": [],
            "ready_to_resume": [],
            "queue": [],
            "completed": [],
            "blocked": [],
            "failed": [],
            "other": [],
        }
        for task in tasks:
            if task.state in ACTIVE_SLOT_STATES:
                summary["active_slots"].append(task)
            elif task.state == TaskState.WAITING_FOR_ANSWER:
                summary["waiting_for_answer"].append(task)
            elif task.state == TaskState.READY_TO_RESUME:
                summary["ready_to_resume"].append(task)
            elif task.state == TaskState.QUEUED:
                summary["queue"].append(task)
            elif task.state == TaskState.COMPLETED:
                summary["completed"].append(task)
            elif task.state == TaskState.BLOCKED:
                summary["blocked"].append(task)
            elif task.state == TaskState.FAILED:
                summary["failed"].append(task)
            elif task.state not in SLOT_FREE_STATES:
                summary["other"].append(task)
        return summary
