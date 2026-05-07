from __future__ import annotations

from typing import Any


RESPONSIBILITY_MATRIX: dict[str, dict[str, Any]] = {
    "generator": {
        "problem_types": {
            "missing_output",
            "missing_attachments",
            "generation_failed",
            "template_error",
            "invalid_output_format",
        },
        "description": "Генератор отвечает за сборку комплекта документов, корректность шаблонов и наличие итоговых файлов.",
    },
    "philologist": {
        "problem_types": {
            "review_generated_documents",
            "grammar_errors",
            "style_inconsistency",
            "review_before_send",
        },
        "description": "Филолог отвечает за языковую корректность и качество формулировок в документах.",
    },
    "sender": {
        "problem_types": {
            "smtp_config_error",
            "send_failed",
            "recipient_confirmation_required",
            "delivery_blocked",
        },
        "description": "Отправщик отвечает за SMTP, выбор адресата и безопасную доставку готового комплекта.",
    },
    "parser": {
        "problem_types": {
            "recipient_data_missing",
            "email_enrichment_required",
            "source_data_missing",
        },
        "description": "Парсер отвечает за исходные данные и дозаполнение недостающих полей.",
    },
    "orchestrator": {
        "problem_types": {
            "workflow_stuck",
            "unknown_problem_type",
            "agent_timeout",
        },
        "description": "Оркестратор отвечает за зависшие сценарии и неразобранные ошибки.",
    },
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def diagnose_responsibility(*, symptom: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    symptom = _safe_text(symptom)
    context = context or {}
    folder_exists = bool(context.get("folder_exists"))
    attachment_count = int(context.get("attachment_count", 0) or 0)
    smtp_configured = bool(context.get("smtp_configured"))
    has_primary_email = bool(context.get("has_primary_email"))
    has_any_valid_email = bool(context.get("has_any_valid_email"))
    unresolved_issue_count = int(context.get("unresolved_issue_count", 0) or 0)

    if symptom == "missing_output":
        return {
            "owner_agent": "generator",
            "problem_type": "missing_output",
            "root_cause": "У отправщика нет папки output по строке, значит комплект документов не был собран генератором.",
            "priority": "high",
            "blocking": True,
            "can_retry_after": True,
        }

    if symptom == "missing_attachments":
        if folder_exists and attachment_count < 2:
            root_cause = "Папка строки существует, но комплект PDF неполный. Это зона генератора и итоговой сборки файлов."
        else:
            root_cause = "Отправщик не нашёл полный комплект вложений, поэтому сначала нужно восстановить документы у генератора."
        return {
            "owner_agent": "generator",
            "problem_type": "missing_attachments",
            "root_cause": root_cause,
            "priority": "high",
            "blocking": True,
            "can_retry_after": True,
        }

    if symptom == "missing_recipient_data":
        if has_primary_email or has_any_valid_email:
            root_cause = "У строки есть данные адресата, но часть email невалидна. Нужна проверка исходных данных."
        else:
            root_cause = "В строке нет валидного email, это проблема слоя данных, а не доставки."
        return {
            "owner_agent": "parser",
            "problem_type": "recipient_data_missing",
            "root_cause": root_cause,
            "priority": "high",
            "blocking": True,
            "can_retry_after": True,
        }

    if symptom == "philology_review_block":
        return {
            "owner_agent": "philologist",
            "problem_type": "review_before_send",
            "root_cause": (
                f"Перед отправкой остались замечания филолога ({unresolved_issue_count}), "
                "поэтому сначала нужно завершить языковую правку."
            ),
            "priority": "high",
            "blocking": True,
            "can_retry_after": True,
        }

    if symptom == "smtp_disabled" or symptom == "smtp_send_failed":
        root_cause = (
            "SMTP не настроен или доставка не удалась на уровне отправки."
            if not smtp_configured
            else "Ошибка возникла на этапе SMTP-доставки, это зона отправщика."
        )
        return {
            "owner_agent": "sender",
            "problem_type": "send_failed",
            "root_cause": root_cause,
            "priority": "medium",
            "blocking": True,
            "can_retry_after": True,
        }

    if symptom == "documents_ready_for_review":
        return {
            "owner_agent": "philologist",
            "problem_type": "review_generated_documents",
            "root_cause": "Генератор собрал комплект документов, теперь следующий ответственный слой — языковая проверка.",
            "priority": "medium",
            "blocking": False,
            "can_retry_after": False,
        }

    return {
        "owner_agent": "orchestrator",
        "problem_type": "unknown_problem_type",
        "root_cause": "По текущему симптому не удалось надёжно определить владельца проблемы.",
        "priority": "medium",
        "blocking": True,
        "can_retry_after": False,
    }
