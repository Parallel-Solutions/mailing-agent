from __future__ import annotations

import hashlib
import logging
import os
import signal
import time
from typing import Any

from src.bitrix_board.agent_runner import (
    AI_QUESTION_PREFIX,
    format_questions_message,
    is_process_alive,
    load_agent_result,
    spawn_agent,
)
from src.bitrix_board.bitrix_client import (
    BitrixClient,
    add_task_message,
    create_bitrix_client,
    get_task,
    get_task_checklist,
    list_task_messages,
    list_tasks_in_stages,
    move_task_to_stage,
)
from src.bitrix_board.config import BoardConfig, load_config
from src.bitrix_board.prompts import (
    build_completion_report,
    build_implement_prompt,
    build_plan_prompt,
    build_review_prompt,
)
from src.bitrix_board.states import TaskState
from src.bitrix_board.store import BoardRun, BoardStore, BoardTask
from src.bitrix_board.worktree import (
    ensure_worktree,
    git_diff_summary,
    load_task_state,
    save_task_state,
    task_log_dir,
    task_state_path,
)

logger = logging.getLogger(__name__)


class BoardDispatcher:
    def __init__(self, config: BoardConfig, store: BoardStore, bitrix: BitrixClient) -> None:
        self.config = config
        self.store = store
        self.bitrix = bitrix
        self._last_poll_at = 0.0
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def start_run(
        self,
        *,
        project: str,
        source_stage: str,
        target_stage: str,
        concurrency: int,
    ) -> BoardRun:
        tasks = list_tasks_in_stages(
            self.bitrix,
            self.config,
            project=project,
            stages=[source_stage],
        )
        snapshot_ids = sorted(task.id for task in tasks)
        titles = {task.id: task.title for task in tasks}
        return self.store.create_run(
            project=project,
            source_stage=source_stage,
            target_stage=target_stage,
            concurrency=max(1, concurrency),
            snapshot_task_ids=snapshot_ids,
            task_titles=titles,
        )

    def run_loop(self, run_id: int) -> None:
        run = self.store.get_run(run_id)
        self.store.set_dispatcher_pid(run_id, os.getpid())
        self.config.dispatcher_pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.dispatcher_pid_path.write_text(str(os.getpid()), encoding="utf-8")

        try:
            while not self._stop:
                run = self.store.get_run(run_id)
                if run.stop_requested:
                    logger.info("Stop requested for run %s", run_id)
                    break

                self._reconcile_finished_agents(run_id)
                self._poll_waiting_tasks(run_id)
                self._fill_slots(run_id)

                tasks = self.store.list_tasks(run_id)
                if all(task.state in {TaskState.COMPLETED, TaskState.BLOCKED, TaskState.FAILED} for task in tasks):
                    logger.info("Run %s completed", run_id)
                    break

                time.sleep(2)
        finally:
            self.store.set_dispatcher_pid(run_id, None)
            if self.config.dispatcher_pid_path.exists():
                self.config.dispatcher_pid_path.unlink(missing_ok=True)

    def _fill_slots(self, run_id: int) -> None:
        run = self.store.get_run(run_id)
        if run.stop_requested:
            return

        while True:
            active = self.store.count_active_slots(run_id)
            if active >= run.concurrency:
                return

            task = self.store.next_ready_to_resume_task(run_id)
            if task:
                self._advance_task(run, task)
                continue

            task = self.store.next_queued_task(run_id)
            if not task:
                return
            self._advance_task(run, task)

    def _advance_task(self, run: BoardRun, task: BoardTask) -> None:
        if task.agent_pid and is_process_alive(task.agent_pid):
            return

        if task.state == TaskState.QUEUED:
            self.store.update_task(task.id, state=TaskState.PREPARING)
            task = self.store.get_task_by_bitrix_id(run.id, task.task_id) or task

        if task.state == TaskState.PREPARING:
            self._prepare_task(run, task)
            return

        if task.state == TaskState.PLANNING:
            self._spawn_planning(run, task)
            return

        if task.state == TaskState.READY_TO_RESUME:
            self.store.update_task(task.id, state=TaskState.IMPLEMENTING, agent_pid=None)
            refreshed = self.store.get_task_by_bitrix_id(run.id, task.task_id)
            if refreshed:
                self._spawn_implementation(run, refreshed)
            return

        if task.state in {TaskState.IMPLEMENTING, TaskState.REWORKING}:
            self._spawn_implementation(run, task)
            return

        if task.state == TaskState.TESTING:
            self.store.update_task(task.id, state=TaskState.REVIEWING, agent_pid=None)
            refreshed = self.store.get_task_by_bitrix_id(run.id, task.task_id)
            if refreshed:
                self._spawn_review(run, refreshed)
            return

        if task.state == TaskState.REVIEWING:
            self._spawn_review(run, task)
            return

    def _prepare_task(self, run: BoardRun, task: BoardTask) -> None:
        try:
            path, branch = ensure_worktree(self.config, task.task_id)
            state_path = task_state_path(path)
            log_dir = task_log_dir(path)
            self.store.update_task(
                task.id,
                worktree_path=str(path),
                branch=branch,
                state_path=str(state_path),
                log_path=str(log_dir),
                state=TaskState.PLANNING,
            )
        except Exception as exc:
            logger.exception("Failed to prepare worktree for task %s", task.task_id)
            self.store.update_task(
                task.id,
                state=TaskState.FAILED,
                error_message=str(exc),
                agent_pid=None,
            )

    def _spawn_planning(self, run: BoardRun, task: BoardTask) -> None:
        if not task.worktree_path:
            self.store.update_task(task.id, state=TaskState.PREPARING, agent_pid=None)
            return

        bitrix_task = get_task(self.bitrix, task.task_id)
        checklist = get_task_checklist(self.bitrix, task.task_id)
        messages = list_task_messages(self.bitrix, task.task_id)
        prompt = build_plan_prompt(
            task=bitrix_task,
            checklist=checklist,
            messages=messages,
            project=run.project,
            source_stage=run.source_stage,
            target_stage=run.target_stage,
            worktree_path=task.worktree_path,
            resume=bool(task.clarifications_json),
            previous_plan=task.plan_json,
            clarifications=task.clarifications_json,
        )
        spawned = spawn_agent(
            self.config,
            worktree=task.worktree_path,  # type: ignore[arg-type]
            prompt=prompt,
            phase="planning",
            mode="plan",
        )
        self.store.update_task(
            task.id,
            agent_pid=spawned.pid,
            log_path=str(spawned.log_path),
            report_json={**(task.report_json or {}), "_agent_phase": "planning"},
        )

    def _handle_planning_result(self, run: BoardRun, task: BoardTask, result) -> None:
        parsed = result.parsed or {}
        plan_payload = {
            "expected_outcome": parsed.get("expected_outcome"),
            "requirements": parsed.get("requirements") or [],
            "acceptance_criteria": parsed.get("acceptance_criteria") or [],
            "plan": parsed.get("plan") or result.stdout,
            "tests": parsed.get("tests") or [],
        }
        if task.state_path:
            save_task_state(
                task.state_path,  # type: ignore[arg-type]
                {"phase": "planning", "plan": plan_payload, "parsed": parsed},
            )

        blocking_questions = [str(q) for q in (parsed.get("blocking_questions") or []) if str(q).strip()]
        status = str(parsed.get("status") or "").lower()

        if result.exit_code != 0 and not parsed:
            self.store.update_task(
                task.id,
                state=TaskState.FAILED,
                error_message=result.stderr or "Planning agent failed",
                agent_pid=None,
            )
            return

        if blocking_questions or status == "blocked":
            refreshed = self.store.get_task_by_bitrix_id(run.id, task.task_id) or task
            self._send_questions(refreshed, blocking_questions or ["Требуется уточнение по задаче."])
            self.store.update_task(
                task.id,
                state=TaskState.WAITING_FOR_ANSWER,
                plan_json=plan_payload,
                questions_json={"questions": blocking_questions},
                agent_pid=None,
            )
            return

        self.store.update_task(
            task.id,
            state=TaskState.IMPLEMENTING,
            plan_json=plan_payload,
            questions_json=None,
            agent_pid=None,
        )

    def _spawn_implementation(self, run: BoardRun, task: BoardTask) -> None:
        if not task.worktree_path or not task.plan_json:
            self.store.update_task(task.id, state=TaskState.FAILED, error_message="Missing worktree or plan")
            return

        rework_feedback = None
        if task.state == TaskState.REWORKING and task.report_json:
            review = task.report_json.get("last_review") or {}
            issues = review.get("issues") or []
            rework_feedback = "\n".join(f"- {issue}" for issue in issues)

        bitrix_task = get_task(self.bitrix, task.task_id)
        prompt = build_implement_prompt(
            task=bitrix_task,
            plan=task.plan_json,
            worktree_path=task.worktree_path,
            rework_feedback=rework_feedback,
            clarifications=task.clarifications_json,
        )
        spawned = spawn_agent(
            self.config,
            worktree=task.worktree_path,  # type: ignore[arg-type]
            prompt=prompt,
            phase="implement" if task.state != TaskState.REWORKING else "rework",
            force=True,
        )
        self.store.update_task(
            task.id,
            agent_pid=spawned.pid,
            log_path=str(spawned.log_path),
            report_json={**(task.report_json or {}), "_agent_phase": "implement"},
        )

    def _handle_implementation_result(self, run: BoardRun, task: BoardTask, result) -> None:
        parsed = result.parsed or {}
        implementation = {
            "summary": parsed.get("summary") or result.stdout[:4000],
            "changed_files": parsed.get("changed_files") or [],
            "test_results": parsed.get("test_results"),
            "lint_results": parsed.get("lint_results"),
            "build_results": parsed.get("build_results"),
            "commits": parsed.get("commits") or [],
        }
        if task.state_path:
            save_task_state(
                task.state_path,  # type: ignore[arg-type]
                load_task_state(task.state_path) | {"phase": "implement", "implementation": implementation},
            )

        if result.exit_code != 0 and str(parsed.get("status", "")).lower() != "done":
            self.store.update_task(
                task.id,
                state=TaskState.FAILED,
                error_message=result.stderr or "Implementation agent failed",
                report_json={**(task.report_json or {}), "implementation": implementation},
                agent_pid=None,
            )
            return

        self.store.update_task(
            task.id,
            state=TaskState.REVIEWING,
            report_json={**(task.report_json or {}), "implementation": implementation},
            agent_pid=None,
        )

    def _spawn_review(self, run: BoardRun, task: BoardTask) -> None:
        if not task.worktree_path or not task.plan_json:
            self.store.update_task(task.id, state=TaskState.FAILED, error_message="Missing worktree or plan")
            return

        bitrix_task = get_task(self.bitrix, task.task_id)
        diff_summary = git_diff_summary(task.worktree_path)  # type: ignore[arg-type]
        prompt = build_review_prompt(
            task=bitrix_task,
            plan=task.plan_json,
            diff_summary=diff_summary,
            worktree_path=task.worktree_path,
        )
        spawned = spawn_agent(
            self.config,
            worktree=task.worktree_path,  # type: ignore[arg-type]
            prompt=prompt,
            phase=f"review-{task.review_cycle + 1}",
            mode="ask",
        )
        self.store.update_task(
            task.id,
            agent_pid=spawned.pid,
            log_path=str(spawned.log_path),
            report_json={**(task.report_json or {}), "_agent_phase": "review"},
        )

    def _handle_review_result(self, run: BoardRun, task: BoardTask, result) -> None:
        parsed = result.parsed or {}
        review = {
            "status": parsed.get("status") or "changes_requested",
            "summary": parsed.get("summary") or result.stdout[:2000],
            "issues": parsed.get("issues") or [],
        }

        if str(review["status"]).lower() == "approved":
            self._complete_task(run, task, review)
            return

        next_cycle = task.review_cycle + 1
        if next_cycle >= self.config.max_review_cycles:
            reason = (
                "Превышен лимит циклов исправлений после ревью.\n"
                "Последние замечания:\n"
                + "\n".join(f"- {issue}" for issue in review.get("issues") or [])
            )
            add_task_message(self.bitrix, task.task_id, reason)
            self.store.update_task(
                task.id,
                state=TaskState.BLOCKED,
                review_cycle=next_cycle,
                report_json={**(task.report_json or {}), "last_review": review},
                error_message=reason,
                agent_pid=None,
            )
            return

        self.store.update_task(
            task.id,
            state=TaskState.REWORKING,
            review_cycle=next_cycle,
            report_json={**(task.report_json or {}), "last_review": review},
            agent_pid=None,
        )

    def _complete_task(self, run: BoardRun, task: BoardTask, review: dict[str, Any]) -> None:
        implementation = (task.report_json or {}).get("implementation") or {}
        report = build_completion_report(
            task={"TITLE": task.title},
            branch=task.branch or "",
            plan=task.plan_json or {},
            implementation=implementation,
            review=review,
        )
        add_task_message(self.bitrix, task.task_id, report)
        move_task_to_stage(self.bitrix, task.task_id, run.target_stage)
        self.store.update_task(
            task.id,
            state=TaskState.COMPLETED,
            report_json={**(task.report_json or {}), "last_review": review, "completion_report": report},
            agent_pid=None,
        )

    def _send_questions(self, task: BoardTask, questions: list[str]) -> None:
        message = format_questions_message(questions)
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        if task.last_question_hash == message_hash:
            return
        message_id = add_task_message(self.bitrix, task.task_id, message)
        self.store.update_task(
            task.id,
            last_question_hash=message_hash,
            last_chat_message_id=str(message_id) if message_id is not None else task.last_chat_message_id,
        )

    def _poll_waiting_tasks(self, run_id: int) -> None:
        now = time.time()
        if now - self._last_poll_at < self.config.poll_interval_seconds:
            return
        self._last_poll_at = now

        waiting_tasks = self.store.tasks_by_state(run_id, TaskState.WAITING_FOR_ANSWER)
        for task in waiting_tasks:
            self._check_task_answers(task)

    def _check_task_answers(self, task: BoardTask) -> None:
        messages = list_task_messages(self.bitrix, task.task_id)
        webhook_user_id = str(self.bitrix.webhook_user_id)
        new_answers: list[dict[str, Any]] = []

        for message in messages:
            author_id = str(message.author_id) if message.author_id is not None else ""
            text = message.plain_text or message.text or ""
            if not text.strip():
                continue
            if author_id == webhook_user_id:
                continue
            if text.strip().startswith(AI_QUESTION_PREFIX):
                continue
            if task.last_chat_message_id and str(message.id) == str(task.last_chat_message_id):
                continue

            message_key = str(message.id) if message.id is not None else f"{message.date}:{text[:80]}"
            known = {item.get("message_id") for item in (task.clarifications_json or [])}
            if message_key in known:
                continue
            new_answers.append(
                {
                    "message_id": message_key,
                    "author_id": author_id,
                    "author_name": message.author_name,
                    "date": message.date,
                    "text": text,
                }
            )

        if not new_answers:
            return

        questions = (task.questions_json or {}).get("questions") or []
        combined = (task.clarifications_json or []) + new_answers
        if questions and len(combined) < len(questions):
            remaining = questions[len(combined) :]
            self._send_questions(task, [str(q) for q in remaining])
            self.store.update_task(task.id, clarifications_json=combined)
            return

        self.store.update_task(
            task.id,
            state=TaskState.READY_TO_RESUME,
            clarifications_json=combined,
            agent_pid=None,
        )

    def _reconcile_finished_agents(self, run_id: int) -> None:
        run = self.store.get_run(run_id)
        tasks = self.store.list_tasks(run_id)
        for task in tasks:
            if not task.agent_pid:
                continue
            if is_process_alive(task.agent_pid):
                continue

            from pathlib import Path

            log_path = Path(task.log_path) if task.log_path else None
            result = load_agent_result(log_path) if log_path else None
            if log_path:
                with log_path.open("a", encoding="utf-8") as log_file:
                    code = result.exit_code if result else 1
                    log_file.write(f"\n=== EXIT CODE: {code} ===\n")

            if not result:
                self.store.update_task(
                    task.id,
                    state=TaskState.FAILED,
                    error_message="Agent finished without readable log output",
                    agent_pid=None,
                )
                continue

            phase = (task.report_json or {}).get("_agent_phase")
            if task.state == TaskState.PLANNING or phase == "planning":
                self._handle_planning_result(run, task, result)
            elif task.state in {TaskState.IMPLEMENTING, TaskState.REWORKING} or phase == "implement":
                self._handle_implementation_result(run, task, result)
            elif task.state == TaskState.REVIEWING or phase == "review":
                self._handle_review_result(run, task, result)
            else:
                self.store.update_task(task.id, agent_pid=None)


def run_dispatcher(
    *,
    project: str,
    source_stage: str,
    target_stage: str,
    concurrency: int,
    resume_run_id: int | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    store = BoardStore(config.db_path)
    bitrix = create_bitrix_client(config)

    try:
        if resume_run_id is not None:
            run = store.get_run(resume_run_id)
            store.clear_stop(resume_run_id)
        else:
            active = store.get_active_run()
            if active:
                logger.error("Active run %s already exists. Stop it first.", active.id)
                return 1
            run = BoardDispatcher(config, store, bitrix).start_run(
                project=project,
                source_stage=source_stage,
                target_stage=target_stage,
                concurrency=concurrency,
            )
            logger.info("Created run %s with %s tasks", run.id, len(run.snapshot_task_ids))

        dispatcher = BoardDispatcher(config, store, bitrix)

        def _handle_signal(signum: int, frame: object) -> None:
            logger.info("Received signal %s, stopping dispatcher", signum)
            store.request_stop(run.id)
            dispatcher.request_stop()

        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)

        dispatcher.run_loop(run.id)
        return 0
    finally:
        bitrix.close()
        store.close()
