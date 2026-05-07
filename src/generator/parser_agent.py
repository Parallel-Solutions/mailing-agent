from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.generator.agent_handoff import (
    count_tasks_for_agent,
    get_tasks_for_agent,
    mark_tasks_in_progress,
)
from src.generator.excel_io import load_rows
from src.generator.config_generator import DATA_XLSX_PATH
from src.generator.ai_case_agent import (
    OpenAI,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.utils.config import settings


PARSER_STATE: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "summary_text": "Агент-парсер ещё не запускался.",
    "task_stats": {"total": 0, "pending": 0, "in_progress": 0, "done": 0},
    "tasks": [],
    "row_count": 0,
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_llm_client() -> OpenAI | None:
    if not OpenAI:
        return None
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    try:
        return OpenAI(**kwargs)
    except Exception:
        return None


def _row_count() -> int:
    if not DATA_XLSX_PATH.exists():
        return 0
    _, _, rows = load_rows(DATA_XLSX_PATH)
    return len(rows)


def run_parser_agent(*, limit: int | None = None) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    claimed_tasks = mark_tasks_in_progress("parser", limit=limit)
    task_stats = count_tasks_for_agent("parser")
    tasks = get_tasks_for_agent("parser")
    state = PARSER_STATE
    state.update(
        {
            "status": "completed",
            "started_at": started_at,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "summary_text": (
                f"Агент-парсер принял {len(claimed_tasks)} задач на дозаполнение. "
                f"Всего в очереди: {task_stats['total']}, ожидают: {task_stats['pending']}, "
                f"в работе: {task_stats['in_progress']}."
            ),
            "task_stats": task_stats,
            "tasks": tasks[:50],
            "row_count": _row_count(),
        }
    )
    return dict(state)


def get_parser_status() -> dict[str, Any]:
    state = dict(PARSER_STATE)
    state["task_stats"] = count_tasks_for_agent("parser")
    state["tasks"] = get_tasks_for_agent("parser")[:50]
    state["row_count"] = _row_count()
    if state["status"] == "idle":
        state["summary_text"] = (
            f"В data.xlsx сейчас {state['row_count']} строк. "
            f"Запросов на дозаполнение: {state['task_stats']['total']}."
        )
    return state


def _fallback_parser_chat(message: str, state: dict[str, Any]) -> str:
    lowered = message.lower()
    tasks = state.get("tasks") or []
    if "email" in lowered or "дозап" in lowered or "задач" in lowered:
        if not tasks:
            return "Сейчас в очереди парсера нет задач на дозаполнение."
        lines = [
            f"{task.get('row_id')} {task.get('mun_name')}: {task.get('task_type')} ({task.get('status')})"
            for task in tasks[:5]
        ]
        return "Очередь парсера:\n" + "\n".join(lines)
    return state.get("summary_text") or "Статус агента-парсера пока недоступен."


def chat_with_parser(message: str) -> dict[str, Any]:
    state = get_parser_status()
    client = _build_llm_client()
    if not client:
        return {"reply": _fallback_parser_chat(message, state), "state": state}

    compact_tasks = []
    for item in (state.get("tasks") or [])[:20]:
        compact_tasks.append(
            {
                "row_id": item.get("row_id"),
                "mun_name": item.get("mun_name"),
                "task_type": item.get("task_type"),
                "status": item.get("status"),
                "details": item.get("details"),
            }
        )

    prompt = (
        "Ты агент-парсер. Отвечай кратко, по-русски, только на основе текущего состояния задач. "
        "Если отправщик запросил дозаполнение email, объясни, что задача принята или сколько таких задач сейчас в очереди.\n\n"
        f"Состояние:\n{json.dumps({'summary_text': state.get('summary_text'), 'task_stats': state.get('task_stats'), 'tasks': compact_tasks}, ensure_ascii=False, indent=2)}\n\n"
        f"Вопрос пользователя:\n{message}"
    )

    request_kwargs = {
        "model": settings.case_agent_model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if not _resolve_openai_base_url():
        request_kwargs["response_format"] = {"type": "text"}

    try:
        response = client.chat.completions.create(**request_kwargs)
        reply = _safe_text(response.choices[0].message.content)
        if not reply:
            reply = _fallback_parser_chat(message, state)
    except Exception:
        reply = _fallback_parser_chat(message, state)

    return {"reply": reply, "state": state}
