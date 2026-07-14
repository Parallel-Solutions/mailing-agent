from __future__ import annotations

import json
from typing import Any

from src.bitrix_board.bitrix_client import ChecklistItem, TaskMessage, TaskSummary

SAFETY_RULES = """
## Safety rules (mandatory)
- Do NOT checkout or modify the main/default branch of the repository.
- Do NOT delete production data.
- Do NOT perform deployment.
- Do NOT force push.
- Do NOT reveal secrets.
- Do NOT modify `.env` files with real credentials.
- Do NOT perform irreversible actions without explicit confirmation.
- Work only inside the assigned worktree directory.
- Create local git commits only; do NOT push unless explicitly configured elsewhere.
""".strip()


def _format_checklist(items: list[ChecklistItem]) -> str:
    if not items:
        return "(empty)"
    lines = []
    for item in items:
        mark = "x" if item.is_complete else " "
        lines.append(f"- [{mark}] {item.title}")
    return "\n".join(lines)


def _format_messages(messages: list[TaskMessage]) -> str:
    if not messages:
        return "(no messages)"
    lines = []
    for message in messages:
        author = message.author_name or message.author_id or "unknown"
        text = message.plain_text or message.text or ""
        lines.append(f"[{message.date}] {author}: {text}")
    return "\n".join(lines)


def build_plan_prompt(
    *,
    task: dict[str, Any],
    checklist: list[ChecklistItem],
    messages: list[TaskMessage],
    project: str,
    source_stage: str,
    target_stage: str,
    worktree_path: str,
    resume: bool = False,
    previous_plan: dict[str, Any] | None = None,
    clarifications: list[dict[str, Any]] | None = None,
) -> str:
    title = task.get("TITLE") or task.get("title") or ""
    description = task.get("DESCRIPTION") or task.get("description") or ""
    resume_block = ""
    if resume and previous_plan:
        resume_block = (
            "\n## Previous plan\n"
            f"{json.dumps(previous_plan, ensure_ascii=False, indent=2)}\n"
            "\n## Clarifications received\n"
            f"{json.dumps(clarifications or [], ensure_ascii=False, indent=2)}\n"
        )

    return f"""
You are a Cursor planning agent for a Bitrix24 task.

{SAFETY_RULES}

## Task context
- Title: {title}
- Project: {project}
- Source stage: {source_stage}
- Target stage: {target_stage}
- Worktree path: {worktree_path}

## Description
{description}

## Checklist
{_format_checklist(checklist)}

## Task chat history
{_format_messages(messages)}
{resume_block}

## Required planning workflow
1. Study the task.
2. Study the relevant part of the project in the worktree.
3. Define the expected outcome.
4. Extract requirements.
5. Define acceptance criteria.
6. Build a change plan.
7. Identify required tests.
8. Find blocking ambiguities.

Ask questions ONLY when you cannot safely choose an implementation without an answer.
Do NOT ask questions if the answer is available from code, project rules, similar features, task description, chat history, or docs.

## Response format
Return your analysis and end with a fenced JSON block:

```json
{{
  "status": "ready" or "blocked",
  "expected_outcome": "...",
  "requirements": ["..."],
  "acceptance_criteria": ["..."],
  "plan": "...",
  "tests": ["..."],
  "blocking_questions": ["..."]
}}
```

Use `"status": "blocked"` only when `blocking_questions` is non-empty.
""".strip()


def build_implement_prompt(
    *,
    task: dict[str, Any],
    plan: dict[str, Any],
    worktree_path: str,
    rework_feedback: str | None = None,
    clarifications: list[dict[str, Any]] | None = None,
) -> str:
    rework_block = ""
    if rework_feedback:
        rework_block = f"\n## Review feedback to address\n{rework_feedback}\n"
    clarifications_block = ""
    if clarifications:
        clarifications_block = (
            "\n## Clarifications from Bitrix24 chat\n"
            f"{json.dumps(clarifications, ensure_ascii=False, indent=2)}\n"
        )

    return f"""
You are a Cursor implementation agent for a Bitrix24 task.

{SAFETY_RULES}

## Worktree
{worktree_path}

## Task
{task.get("TITLE") or task.get("title")}

## Approved plan
{json.dumps(plan, ensure_ascii=False, indent=2)}
{clarifications_block}{rework_block}

## Required workflow
1. Implement the approved plan.
2. Add or update tests.
3. Run tests.
4. Run linters.
5. Run build if applicable.
6. Fix discovered errors.
7. Create local git commits for completed work.
8. Prepare an implementation report.

Return a fenced JSON block:

```json
{{
  "status": "done" or "failed",
  "summary": "...",
  "changed_files": ["..."],
  "test_results": "...",
  "lint_results": "...",
  "build_results": "...",
  "commits": ["..."]
}}
```
""".strip()


def build_review_prompt(
    *,
    task: dict[str, Any],
    plan: dict[str, Any],
    diff_summary: str,
    worktree_path: str,
) -> str:
    return f"""
You are an independent code reviewer. Do NOT continue the implementer's reasoning.

{SAFETY_RULES}

## Worktree
{worktree_path}

## Original task
{task.get("TITLE") or task.get("title")}

## Description
{task.get("DESCRIPTION") or task.get("description") or ""}

## Approved plan and acceptance criteria
{json.dumps(plan, ensure_ascii=False, indent=2)}

## Git diff summary
{diff_summary}

Review for:
- match to original task
- acceptance criteria
- business logic correctness
- implementation quality
- tests
- errors
- security
- backward compatibility
- absence of unplanned changes

Return fenced JSON:

```json
{{
  "status": "approved" or "changes_requested",
  "summary": "...",
  "issues": ["..."]
}}
```
""".strip()


def build_completion_report(
    *,
    task: TaskSummary | dict[str, Any],
    branch: str,
    plan: dict[str, Any],
    implementation: dict[str, Any],
    review: dict[str, Any],
) -> str:
    title = task.title if isinstance(task, TaskSummary) else str(task.get("TITLE") or task.get("title"))
    changed_files = implementation.get("changed_files") or []
    files_block = "\n".join(f"- {path}" for path in changed_files) or "- (see git diff)"
    return f"""ИИ завершил задачу.

## Что реализовано
{implementation.get("summary") or plan.get("expected_outcome") or "См. план и diff."}

## Ветка
`{branch}`

## Основные изменённые файлы
{files_block}

## Результаты тестов
{implementation.get("test_results") or "не указано"}

## Результат сборки
{implementation.get("build_results") or "не указано"}

## Результат ревью
{review.get("summary") or "approved"}

## Ручная проверка
1. Откройте worktree ветки `{branch}`.
2. Проверьте изменения по задаче «{title}».
3. Запустите тесты локально при необходимости.
4. Выполните merge в основную ветку вручную после проверки.
""".strip()
