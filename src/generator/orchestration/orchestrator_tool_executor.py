from __future__ import annotations

from pathlib import Path
from typing import Any

from src.generator.generation.generator_agent import run_generator_agent
from src.generator.orchestration.orchestrator_session_state import UserGoalState
from src.generator.orchestration.parser_agent import run_parser_agent
from src.generator.philologist.philologist_agent import run_philologist
from src.generator.delivery.sender_agent import run_sender
from src.generator.generation.config_generator import OUTPUT_DIR


def _normalize_result(
    *,
    success: bool,
    summary: str,
    details: dict[str, Any] | None = None,
    next_recommendation: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "summary": summary,
        "details": details or {},
        "next_recommendation": next_recommendation,
        "error": error,
    }


def _readiness_result(
    *,
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    analysis: str,
    risks: list[str],
    options: list[str],
) -> dict[str, Any]:
    missing = []
    if not preflight.get("data_loaded"):
        missing.append("data.xlsx")
    if not preflight.get("kp_template_loaded"):
        missing.append("шаблон КП")
    if not preflight.get("contract_template_loaded"):
        missing.append("шаблон договора")
    if not preflight.get("mail_template_loaded"):
        missing.append("шаблон письма")
    if not preflight.get("base_loaded"):
        missing.append("base.xlsx")
    ready = not missing
    summary = "Система выглядит готовой." if ready else f"Не всё готово: не хватает {', '.join(missing)}."
    recommendation = options[0] if options else None
    return _normalize_result(
        success=True,
        summary=summary,
        details={
            "ready": ready,
            "missing": missing,
            "preflight": preflight,
            "snapshot": snapshot,
            "analysis": analysis,
            "risks": risks,
            "options": options,
        },
        next_recommendation=recommendation,
    )


def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    analysis: str,
    risks: list[str],
    options: list[str],
    goal_state: UserGoalState,
) -> dict[str, Any]:
    try:
        if tool_name == "check_system_readiness":
            return _readiness_result(
                preflight=preflight,
                snapshot=snapshot,
                analysis=analysis,
                risks=risks,
                options=options,
            )

        if tool_name == "get_status_summary":
            sender_stats = snapshot.get("sender", {}).get("stats") or {}
            parser_stats = snapshot.get("parser", {}).get("task_stats") or {}
            summary = (
                f"Генератор: {snapshot.get('generator', {}).get('status', 'idle')}, "
                f"Парсер: {snapshot.get('parser', {}).get('status', 'idle')} "
                f"(задач: {parser_stats.get('total', 0)}), "
                f"Филолог: {snapshot.get('philologist', {}).get('status', 'idle')}, "
                f"Отправщик: {snapshot.get('sender', {}).get('status', 'idle')} "
                f"(отправлено: {sender_stats.get('sent', 0)}, ждут: {sender_stats.get('pending', 0)})."
            )
            return _normalize_result(
                success=True,
                summary=summary,
                details={
                    "snapshot": snapshot,
                    "preflight": preflight,
                    "analysis": analysis,
                    "risks": risks,
                    "options": options,
                },
                next_recommendation=options[0] if options else None,
            )

        if tool_name == "run_generator":
            result = run_generator_agent()
            goal_state.status = "executing"
            goal_state.steps_completed.append("run_generator")
            archive_ready = OUTPUT_DIR.exists() and any(OUTPUT_DIR.rglob("*.*"))
            return _normalize_result(
                success=result.get("status") != "error",
                summary=result.get("summary_text") or "Агент-генератор завершил работу.",
                details={
                    **result,
                    "downloads": (
                        [{"label": "Скачать архив output", "url": "/api/download/output"}]
                        if archive_ready
                        else []
                    ),
                },
                next_recommendation="Проверьте результат генерации или переходите к проверке готовности к отправке.",
            )

        if tool_name == "run_parser":
            limit = arguments.get("limit")
            result = run_parser_agent(limit=int(limit) if limit is not None else None)
            goal_state.status = "executing"
            goal_state.steps_completed.append("run_parser")
            return _normalize_result(
                success=result.get("status") != "error",
                summary=result.get("summary_text") or "Агент-парсер завершил работу.",
                details=result,
                next_recommendation="После дозаполнения можно снова проверить готовность к следующему шагу.",
            )

        if tool_name == "run_philologist":
            result = run_philologist(ai_enabled=True)
            goal_state.status = "executing"
            goal_state.steps_completed.append("run_philologist")
            return _normalize_result(
                success=result.get("status") != "error",
                summary=result.get("summary_text") or "Агент-филолог завершил проверку.",
                details=result,
                next_recommendation="Можно изучить замечания или продолжить подготовку к отправке.",
            )

        if tool_name == "run_sender_dry_run":
            limit = arguments.get("limit")
            result = run_sender(dry_run=True, limit=int(limit) if limit is not None else None)
            goal_state.status = "executing"
            goal_state.steps_completed.append("run_sender_dry_run")
            ready_rows = [
                {
                    "id": item.get("id"),
                    "mun_name": item.get("mun_name"),
                    "recipient": item.get("recipient"),
                    "attachments": item.get("attachments", []),
                }
                for item in (result.get("rows") or [])
                if item.get("result") in {"ready", "sent"} and item.get("recipient")
            ]
            goal_state.context["pending_send_confirmation"] = {
                "awaiting": bool(ready_rows),
                "approved": False,
                "rows": ready_rows,
                "checked_at": result.get("completed_at"),
            }
            return _normalize_result(
                success=result.get("status") != "error",
                summary=result.get("summary_text") or "Агент-отправщик завершил dry-run проверку.",
                details={
                    **result,
                    "confirmation_required": bool(ready_rows),
                    "ready_recipients": ready_rows,
                },
                next_recommendation=(
                    "Теперь нужно подтвердить, что найденные почты верные и можно отправлять."
                    if ready_rows
                    else "Сначала нужно исправить проблемы по строкам, готовых адресатов пока нет."
                ),
            )

        if tool_name == "run_sender_send":
            confirmation = goal_state.context.get("pending_send_confirmation") or {}
            if not confirmation.get("approved"):
                return _normalize_result(
                    success=False,
                    summary="Реальную отправку пока нельзя запускать: сначала нужно подтвердить правильность почт.",
                    details={
                        "confirmation_required": True,
                        "ready_recipients": confirmation.get("rows", []),
                    },
                    next_recommendation="Покажи пользователю найденные почты и дождись явного подтверждения.",
                )
            limit = arguments.get("limit")
            result = run_sender(dry_run=False, limit=int(limit) if limit is not None else None)
            goal_state.status = "executing"
            goal_state.steps_completed.append("run_sender_send")
            goal_state.context["pending_send_confirmation"] = {
                "awaiting": False,
                "approved": False,
                "rows": [],
                "checked_at": result.get("completed_at"),
            }
            return _normalize_result(
                success=result.get("status") != "error",
                summary=result.get("summary_text") or "Агент-отправщик выполнил реальную отправку.",
                details=result,
                next_recommendation="Можно проверить статусы отправки и проблемные строки.",
            )

        if tool_name == "get_output_archive_link":
            archive_ready = OUTPUT_DIR.exists() and any(OUTPUT_DIR.rglob("*.*"))
            if not archive_ready:
                return _normalize_result(
                    success=False,
                    summary="Архив пока недоступен: в output ещё нет сгенерированных файлов.",
                    details={"downloads": []},
                    next_recommendation="Сначала нужно сгенерировать документы.",
                )
            return _normalize_result(
                success=True,
                summary="Архив готов к скачиванию.",
                details={
                    "downloads": [
                        {
                            "label": "Скачать архив output",
                            "url": "/api/download/output",
                        }
                    ]
                },
                next_recommendation="Можно скачать архив прямо сейчас.",
            )

        return _normalize_result(
            success=False,
            summary=f"Неизвестный инструмент: {tool_name}",
            error=f"Unknown tool: {tool_name}",
        )
    except Exception as exc:
        return _normalize_result(
            success=False,
            summary=f"Ошибка при выполнении инструмента {tool_name}: {exc}",
            error=str(exc),
        )
