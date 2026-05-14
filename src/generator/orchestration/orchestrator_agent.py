from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.generator.orchestration.agent_handoff import get_recent_events
from src.generator.inflection.ai_case_agent import (
    OpenAI,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.generator.orchestration.orchestrator_agent_loop import run_agentic_orchestrator
from src.generator.orchestration.orchestrator_session_state import append_message, get_goal_state, get_session
from src.generator.generation.config_generator import DATA_DIR, DATA_XLSX_PATH
from src.generator.generation.document_builder import CONTRACT_TEMPLATE_PATH, KP_TEMPLATE_PATH
from src.generator.generation.generator_agent import get_generator_status, run_generator_agent
from src.generator.orchestration.parser_agent import get_parser_status, run_parser_agent
from src.generator.philologist.philologist_agent import get_philologist_status, run_philologist
from src.generator.delivery.sender_agent import MAIL_TEMPLATE_DOCX_PATH, MAIL_TEMPLATE_PATH, get_sender_status, run_sender
from src.utils.config import settings


ORCHESTRATOR_STATE: dict[str, Any] = {
    "status": "idle",
    "last_goal": "",
    "last_action": "",
    "last_plan": [],
    "last_result": "",
    "last_analysis": "",
    "last_risks": [],
    "last_options": [],
    "session_id": None,
    "history_length": 0,
    "goal_state": {},
    "updated_at": None,
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


def _preflight() -> dict[str, Any]:
    data_exists = DATA_XLSX_PATH.exists()
    rows = 0
    if data_exists:
        try:
            from src.generator.generation.excel_io import load_rows

            _, _, loaded_rows = load_rows(DATA_XLSX_PATH)
            rows = len(loaded_rows)
        except Exception:
            rows = 0

    output_dir = DATA_DIR / "output"
    output_folders = len([item for item in output_dir.iterdir() if item.is_dir()]) if output_dir.exists() else 0
    return {
        "data_loaded": data_exists,
        "row_count": rows,
        "kp_template_loaded": KP_TEMPLATE_PATH.exists(),
        "contract_template_loaded": CONTRACT_TEMPLATE_PATH.exists(),
        "mail_template_loaded": MAIL_TEMPLATE_PATH.exists() or MAIL_TEMPLATE_DOCX_PATH.exists(),
        "base_loaded": (DATA_DIR / "base.xlsx").exists(),
        "output_folder_count": output_folders,
        "smtp_configured": bool(settings.smtp_sender_email and settings.smtp_sender_password and settings.smtp_host),
    }


def _build_analysis(preflight: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    risks: list[str] = []
    options: list[str] = []

    if not preflight["data_loaded"]:
        risks.append("Файл data.xlsx ещё не загружен.")
    elif preflight["row_count"] == 0:
        risks.append("data.xlsx загружен, но строк для обработки пока не найдено.")

    if not preflight["kp_template_loaded"] or not preflight["contract_template_loaded"]:
        risks.append("Не все шаблоны документов загружены.")

    if not preflight["mail_template_loaded"]:
        risks.append("Шаблон письма не загружен, сейчас будет использоваться дефолтный текст.")

    if preflight["output_folder_count"] == 0:
        risks.append("В output пока нет папок с готовыми документами.")

    if not preflight["smtp_configured"]:
        risks.append("SMTP ещё не настроен, поэтому реальная отправка пока небезопасна.")

    sender_stats = snapshot["sender"].get("stats") or {}
    if sender_stats.get("pending", 0) > 0:
        options.append("Сначала прогнать dry-run отправщика и понять, какие строки реально готовы к рассылке.")
    if preflight["output_folder_count"] < preflight["row_count"]:
        options.append("Сначала запустить генератора, чтобы добрать недостающие документы в output.")
    if snapshot["parser"].get("task_stats", {}).get("total", 0) > 0:
        options.append("Перед отправкой поднять очередь дозаполнения у парсера, чтобы попытаться добрать email.")
    if snapshot["generator"].get("task_stats", {}).get("pending", 0) > 0:
        options.append("У генератора есть внутренние задачи: можно сначала добрать недостающие комплекты документов.")
    if snapshot["philologist"].get("task_stats", {}).get("pending", 0) > 0:
        options.append("У филолога есть задачи на проверку новых документов перед следующим этапом.")
    if snapshot["sender"].get("task_stats", {}).get("pending", 0) > 0:
        options.append("У отправщика есть внутренние задачи и блокеры: стоит проверить замечания перед отправкой.")
    if not options:
        options.append("Система выглядит готовой к следующему этапу. Можно запускать проверку готовности к отправке.")

    analysis = (
        f"Проверил среду: data.xlsx {'загружен' if preflight['data_loaded'] else 'не загружен'}, "
        f"строк в работе {preflight['row_count']}, папок output {preflight['output_folder_count']}, "
        f"шаблон КП {'есть' if preflight['kp_template_loaded'] else 'нет'}, "
        f"шаблон договора {'есть' if preflight['contract_template_loaded'] else 'нет'}, "
        f"шаблон письма {'есть' if preflight['mail_template_loaded'] else 'нет'}. "
        f"Последних межагентных событий в системе: {len(snapshot.get('agent_events') or [])}."
    )
    return analysis, risks, options


def _build_snapshot() -> dict[str, Any]:
    parser = get_parser_status()
    generator = get_generator_status()
    philologist = get_philologist_status()
    sender = get_sender_status()
    return {
        "parser": {
            "status": parser.get("status"),
            "summary_text": parser.get("summary_text"),
            "task_stats": parser.get("task_stats"),
            "row_count": parser.get("row_count"),
        },
        "generator": {
            "status": generator.get("status"),
            "summary_text": generator.get("summary_text"),
            "total_rows": generator.get("total_rows"),
            "ok_rows": generator.get("ok_rows"),
            "error_rows": generator.get("error_rows"),
            "task_stats": generator.get("task_stats"),
        },
        "philologist": {
            "status": philologist.get("status"),
            "summary_text": philologist.get("summary_text"),
            "processed_documents": philologist.get("processed_documents"),
            "documents_with_issues": philologist.get("documents_with_issues"),
            "task_stats": philologist.get("task_stats"),
        },
        "sender": {
            "status": sender.get("status"),
            "summary_text": sender.get("summary_text"),
            "stats": sender.get("stats"),
            "ready_rows": sender.get("ready_rows"),
            "error_rows": sender.get("error_rows"),
            "handoff_rows": sender.get("handoff_rows"),
            "task_stats": sender.get("task_stats"),
        },
        "agent_events": get_recent_events(limit=15),
    }


def get_orchestrator_status(session_id: str | None = None) -> dict[str, Any]:
    state = dict(ORCHESTRATOR_STATE)
    state["snapshot"] = _build_snapshot()
    state["preflight"] = _preflight()
    if session_id:
        resolved_session_id, session = get_session(session_id)
        goal_state = get_goal_state(session)
        state["session_id"] = resolved_session_id
        state["history_length"] = len(session.get("history", []))
        state["goal_state"] = goal_state.to_dict()
    return state


def _set_state(
    *,
    goal: str,
    action: str,
    plan: list[str],
    result: str,
    analysis: str = "",
    risks: list[str] | None = None,
    options: list[str] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    history_length = 0
    goal_dict: dict[str, Any] = {}
    resolved_session_id = session_id
    if session_id:
        resolved_session_id, session = get_session(session_id)
        history_length = len(session.get("history", []))
        goal_dict = get_goal_state(session).to_dict()
    ORCHESTRATOR_STATE.update(
        {
            "status": "completed",
            "last_goal": goal,
            "last_action": action,
            "last_plan": plan,
            "last_result": result,
            "last_analysis": analysis,
            "last_risks": risks or [],
            "last_options": options or [],
            "session_id": resolved_session_id,
            "history_length": history_length,
            "goal_state": goal_dict,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    return get_orchestrator_status(resolved_session_id)


def _maybe_llm_enhance(goal: str, analysis: str, result: str, options: list[str], risks: list[str]) -> str:
    client = _build_llm_client()
    if not client:
        return ""
    prompt = (
        "Ты ИИ-агент-оркестратор. Сформулируй краткий, но не сухой управленческий ответ пользователю. "
        "Нужно: 1) коротко проанализировать текущее состояние, 2) назвать риски, 3) предложить понятный следующий шаг. "
        "Не выдумывай факты вне входных данных. Отвечай по-русски, 5-8 предложений.\n\n"
        f"Цель пользователя: {goal}\n"
        f"Анализ: {analysis}\n"
        f"Результат действия: {result}\n"
        f"Риски: {risks}\n"
        f"Варианты: {options}\n"
    )
    try:
        response = client.chat.completions.create(
            model=settings.case_agent_model,
            messages=[{"role": "user", "content": prompt}],
        )
        return _safe_text(response.choices[0].message.content)
    except Exception:
        return ""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _safe_text(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _classify_orchestrator_intent(message: str, preflight: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
    client = _build_llm_client()
    if not client:
        return None

    compact_context = {
        "data_loaded": preflight["data_loaded"],
        "row_count": preflight["row_count"],
        "kp_template_loaded": preflight["kp_template_loaded"],
        "contract_template_loaded": preflight["contract_template_loaded"],
        "mail_template_loaded": preflight["mail_template_loaded"],
        "base_loaded": preflight["base_loaded"],
        "output_folder_count": preflight["output_folder_count"],
        "smtp_configured": preflight["smtp_configured"],
        "parser_status": snapshot["parser"].get("status"),
        "generator_status": snapshot["generator"].get("status"),
        "philologist_status": snapshot["philologist"].get("status"),
        "sender_status": snapshot["sender"].get("status"),
    }

    prompt = (
        "Ты роутер для ИИ-оркестратора. Твоя задача — понять намерение пользователя и выбрать одно действие. "
        "Не выдумывай новые действия. Не запускай тяжелые действия без явного запроса. "
        "Если пользователь просто здоровается или пишет small talk, выбери greeting. "
        "Если пользователь спрашивает, все ли загружено, что загружено, чего не хватает, готово ли всё, выбери status_readiness. "
        "Если пользователь просит показать общее состояние системы, выбери status_overview. "
        "Если пользователь просит сгенерировать документы, выбери run_generator_agent. "
        "Если пользователь хочет отправить, но сначала проверить готовность, выбери run_sender_dry_run. "
        "Если пользователь просит подготовить всю цепочку перед отправкой, выбери prepare_send_pipeline. "
        "Если пользователь просит запустить парсер или дозаполнение email, выбери run_parser_agent. "
        "Если пользователь просит проверить документы как филолог, выбери run_philologist. "
        "Если задача непонятна, выбери clarify.\n\n"
        "Верни только JSON-объект формата:\n"
        '{"action":"greeting|status_readiness|status_overview|prepare_send_pipeline|run_generator_agent|run_sender_dry_run|run_parser_agent|run_philologist|clarify","confidence":0.0,"reason":"кратко"}\n\n'
        f"Текущее состояние системы: {json.dumps(compact_context, ensure_ascii=False)}\n"
        f"Сообщение пользователя: {message}"
    )

    try:
        response = client.chat.completions.create(
            model=settings.case_agent_model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return None

    parsed = _extract_json_object(_safe_text(response.choices[0].message.content))
    if not parsed:
        return None
    return parsed


def _default_status_reply() -> tuple[str, list[str]]:
    snapshot = _build_snapshot()
    preflight = _preflight()
    analysis, risks, options = _build_analysis(preflight, snapshot)
    parser_stats = snapshot["parser"].get("task_stats") or {}
    generator_stats = snapshot["generator"] or {}
    sender_stats = snapshot["sender"].get("stats") or {}
    plan = [
        "Собрать статус по внутренним агентам.",
        "Понять, есть ли проблемы на этапе генерации.",
        "Понять, есть ли задачи на дозаполнение у парсера.",
        "Понять, сколько строк уже отправлено и сколько ещё ждут обработки.",
    ]
    reply = (
        f"Генератор: {snapshot['generator'].get('status')} "
        f"(успешно: {generator_stats.get('ok_rows', 0)}, ошибок: {generator_stats.get('error_rows', 0)}). "
        f"Парсер: {snapshot['parser'].get('status')} "
        f"(задач на дозаполнение: {parser_stats.get('total', 0)}). "
        f"Филолог: {snapshot['philologist'].get('status')}. "
        f"Отправщик: {snapshot['sender'].get('status')} "
        f"(отправлено: {sender_stats.get('sent', 0)}, ждут: {sender_stats.get('pending', 0)})."
    )
    rich_reply = _maybe_llm_enhance("показать статус системы", analysis, reply, options, risks)
    return rich_reply or reply, plan, analysis, risks, options


def _build_readiness_reply(preflight: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, list[str], str, list[str], list[str]]:
    parser_stats = snapshot["parser"].get("task_stats") or {}
    sender_stats = snapshot["sender"].get("stats") or {}

    checks = [
        ("data.xlsx", preflight["data_loaded"]),
        ("шаблон КП", preflight["kp_template_loaded"]),
        ("шаблон договора", preflight["contract_template_loaded"]),
        ("шаблон письма", preflight["mail_template_loaded"]),
        ("base.xlsx", preflight["base_loaded"]),
    ]
    ready_items = [label for label, ok in checks if ok]
    missing_items = [label for label, ok in checks if not ok]

    if not missing_items:
        opening = "Да, основная загрузка выглядит полной."
    elif ready_items:
        opening = "Частично. Не всё, что нужно, сейчас загружено."
    else:
        opening = "Пока нет. Ключевые входные файлы ещё не загружены."

    details: list[str] = []
    if ready_items:
        details.append("Есть: " + ", ".join(ready_items) + ".")
    if missing_items:
        details.append("Не хватает: " + ", ".join(missing_items) + ".")

    if preflight["data_loaded"]:
        details.append(
            f"В data.xlsx сейчас {preflight['row_count']} строк, в output уже {preflight['output_folder_count']} папок."
        )

    if parser_stats.get("total", 0) > 0:
        details.append(
            f"У парсера в очереди {parser_stats.get('total', 0)} задач на дозаполнение."
        )

    if not preflight["smtp_configured"]:
        details.append("SMTP пока не настроен, поэтому к реальной отправке система ещё не полностью готова.")
    elif sender_stats.get("pending", 0) > 0:
        details.append(
            f"SMTP настроен, но у отправщика ещё ждут обработки {sender_stats.get('pending', 0)} строк."
        )

    reply = " ".join([opening, *details]).strip()
    analysis, risks, options = _build_analysis(preflight, snapshot)
    plan = [
        "Проверить, какие входные файлы уже загружены.",
        "Сверить наличие шаблонов, таблиц и готовых папок.",
        "Подсказать, чего не хватает до следующего шага.",
    ]
    return reply, plan, analysis, risks, options


def _legacy_chat_with_orchestrator(message: str, session_id: str | None = None) -> dict[str, Any]:
    resolved_session_id, session = get_session(session_id)
    goal_state = get_goal_state(session)
    goal_state.goal = message
    append_message(session, "user", message)

    def _finalize_legacy(reply: str, state: dict[str, Any]) -> dict[str, Any]:
        append_message(session, "assistant", reply)
        return {"reply": reply, "state": state, "session_id": resolved_session_id}

    lowered = message.lower().strip()
    snapshot = _build_snapshot()
    preflight = _preflight()
    analysis, risks, options = _build_analysis(preflight, snapshot)
    llm_route = _classify_orchestrator_intent(message, preflight, snapshot) or {}
    llm_action = _safe_text(llm_route.get("action")) or ""

    if llm_action == "greeting" or lowered in {"привет", "здравствуйте", "добрый день", "доброе утро", "добрый вечер", "ты тут", "приветик"}:
        reply = (
            "Привет! Я на месте. Могу показать статус, помочь с генерацией документов, "
            "проверкой или подготовкой к отправке."
        )
        state = _set_state(
            goal=message,
            action="greeting",
            plan=[],
            result=reply,
            analysis="",
            risks=[],
            options=[],
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    if llm_action == "status_readiness":
        reply, plan, analysis, risks, options = _build_readiness_reply(preflight, snapshot)
        state = _set_state(
            goal=message,
            action="status_readiness",
            plan=plan,
            result=reply,
            analysis=analysis,
            risks=risks,
            options=options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    wants_send = "отправ" in lowered or "рассыл" in lowered
    mentions_not_generated = ("не сгенер" in lowered) or ("еще не сгенер" in lowered) or ("ещё не сгенер" in lowered)
    wants_generate = "сгенер" in lowered and "документ" in lowered

    if llm_action == "status_overview" or any(token in lowered for token in ["статус", "что происходит", "сводк", "обзор"]):
        reply, plan, analysis, risks, options = _default_status_reply()
        state = _set_state(
            goal=message,
            action="status_overview",
            plan=plan,
            result=reply,
            analysis=analysis,
            risks=risks,
            options=options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    if llm_action == "prepare_send_pipeline" or any(token in lowered for token in ["подготов", "цепоч", "план", "готовность по отправке"]) or (wants_send and mentions_not_generated):
        plan = [
            "Сначала передать задачу агенту-генератору.",
            "Затем передать готовые документы агенту-филологу на проверку.",
            "Затем передать задачу агенту-отправщику на проверку готовности.",
            "Если не хватает email, сформировать задачи на дозаполнение для парсера.",
            "Собрать итоговую сводку: что готово к отправке, что требует дозаполнения, где есть ошибки.",
        ]
        generator_result = run_generator_agent()
        review_result = run_philologist(ai_enabled=True)
        sender_result = run_sender(dry_run=True)
        parser_result = run_parser_agent() if sender_result.get("handoff_rows", 0) > 0 else get_parser_status()
        base_reply = (
            f"{generator_result.get('summary_text')} "
            f"{review_result.get('summary_text')} "
            f"{sender_result.get('summary_text')} "
            f"Парсеру передано задач на дозаполнение: {(parser_result.get('task_stats') or {}).get('total', 0)}."
        )
        updated_snapshot = _build_snapshot()
        updated_preflight = _preflight()
        updated_analysis, updated_risks, updated_options = _build_analysis(updated_preflight, updated_snapshot)
        rich_reply = _maybe_llm_enhance(message, updated_analysis, base_reply, updated_options, updated_risks)
        reply = rich_reply or base_reply
        state = _set_state(
            goal=message,
            action="prepare_send_pipeline",
            plan=plan,
            result=reply,
            analysis=updated_analysis,
            risks=updated_risks,
            options=updated_options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    if llm_action == "run_generator_agent" or wants_generate:
        plan = [
            "Передать задачу агенту-генератору.",
            "Собрать документы по строкам data.xlsx.",
            "Вернуть итог по успешно созданным и проблемным строкам.",
        ]
        generator_result = run_generator_agent()
        base_reply = generator_result.get("summary_text") or "Агент-генератор завершил обработку."
        updated_snapshot = _build_snapshot()
        updated_preflight = _preflight()
        updated_analysis, updated_risks, updated_options = _build_analysis(updated_preflight, updated_snapshot)
        rich_reply = _maybe_llm_enhance(message, updated_analysis, base_reply, updated_options, updated_risks)
        reply = rich_reply or base_reply
        state = _set_state(
            goal=message,
            action="run_generator_agent",
            plan=plan,
            result=reply,
            analysis=updated_analysis,
            risks=updated_risks,
            options=updated_options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    if llm_action == "run_sender_dry_run" or ("отправ" in lowered and any(token in lowered for token in ["пров", "готов", "рассылк"])):
        plan = [
            "Передать задачу агенту-отправщику.",
            "Проверить строки data.xlsx без реальной отправки.",
            "Получить готовые к рассылке строки и проблемные кейсы.",
        ]
        sender_result = run_sender(dry_run=True)
        base_reply = sender_result.get("summary_text") or "Агент-отправщик завершил проверку готовности."
        updated_snapshot = _build_snapshot()
        updated_preflight = _preflight()
        updated_analysis, updated_risks, updated_options = _build_analysis(updated_preflight, updated_snapshot)
        rich_reply = _maybe_llm_enhance(message, updated_analysis, base_reply, updated_options, updated_risks)
        reply = rich_reply or base_reply
        state = _set_state(
            goal=message,
            action="run_sender_dry_run",
            plan=plan,
            result=reply,
            analysis=updated_analysis,
            risks=updated_risks,
            options=updated_options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    if llm_action == "run_parser_agent" or ("парсер" in lowered and any(token in lowered for token in ["запус", "дозап", "email", "почт"])):
        plan = [
            "Передать задачу агенту-парсеру.",
            "Поднять очередь задач на дозаполнение.",
            "Вернуть краткую сводку по очереди и задачам в работе.",
        ]
        parser_result = run_parser_agent()
        base_reply = parser_result.get("summary_text") or "Агент-парсер обновил очередь задач."
        updated_snapshot = _build_snapshot()
        updated_preflight = _preflight()
        updated_analysis, updated_risks, updated_options = _build_analysis(updated_preflight, updated_snapshot)
        rich_reply = _maybe_llm_enhance(message, updated_analysis, base_reply, updated_options, updated_risks)
        reply = rich_reply or base_reply
        state = _set_state(
            goal=message,
            action="run_parser_agent",
            plan=plan,
            result=reply,
            analysis=updated_analysis,
            risks=updated_risks,
            options=updated_options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    if llm_action == "run_philologist" or ("филолог" in lowered and any(token in lowered for token in ["запус", "пров", "документ"])):
        plan = [
            "Передать задачу агенту-филологу.",
            "Проверить готовые документы на языковые ошибки.",
            "Собрать краткую сводку по найденным проблемам.",
        ]
        review_result = run_philologist(ai_enabled=True)
        base_reply = review_result.get("summary_text") or "Агент-филолог завершил проверку."
        updated_snapshot = _build_snapshot()
        updated_preflight = _preflight()
        updated_analysis, updated_risks, updated_options = _build_analysis(updated_preflight, updated_snapshot)
        rich_reply = _maybe_llm_enhance(message, updated_analysis, base_reply, updated_options, updated_risks)
        reply = rich_reply or base_reply
        state = _set_state(
            goal=message,
            action="run_philologist",
            plan=plan,
            result=reply,
            analysis=updated_analysis,
            risks=updated_risks,
            options=updated_options,
            session_id=resolved_session_id,
        )
        return _finalize_legacy(reply, state)

    reply = (
        "Пока не до конца понял задачу. Могу показать статус, запустить генерацию документов, "
        "проверить готовность к отправке или помочь собрать следующий шаг."
    )
    plan: list[str] = []
    state = _set_state(
        goal=message,
        action="clarify",
        plan=plan,
        result=reply,
        analysis="",
        risks=[],
        options=[],
        session_id=resolved_session_id,
    )
    return _finalize_legacy(reply, state)


def chat_with_orchestrator(message: str, session_id: str | None = None) -> dict[str, Any]:
    if settings.orchestrator_mode == "agentic":
        try:
            result = run_agentic_orchestrator(
                user_message=message,
                session_id=session_id,
                snapshot_builder=_build_snapshot,
                preflight_builder=_preflight,
                analysis_builder=_build_analysis,
                state_setter=_set_state,
            )
            if result.get("state", {}).get("last_action") == "agentic_unavailable":
                return _legacy_chat_with_orchestrator(message, session_id=session_id)
            return result
        except Exception:
            return _legacy_chat_with_orchestrator(message, session_id=session_id)
    return _legacy_chat_with_orchestrator(message, session_id=session_id)
