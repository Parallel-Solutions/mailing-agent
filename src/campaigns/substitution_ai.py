"""AI-assisted placeholder normalization and system-variable classification."""

from __future__ import annotations

from typing import Any

from src.campaigns.substitution_context import SYSTEM_AUTO_VARIABLES
from src.campaigns.substitution_engine import PlaceholderInfo, discover_placeholders
from src.campaigns.template_ai import _call_llm, list_models

_SYSTEM_CLASSIFY_PROMPT = (
    "Ты классифицируешь переменные шаблонов email/документов для рассылки по муниципальным образованиям. "
    'Верни только JSON: {"system_resolved":[{"template_variable":"...","canonical":"..."}],'
    '"recipient":["..."], "unknown":["..."]} '
    "system_resolved — переменные, которые заполняются автоматически из системы "
    "(DATE, VALID_UNTIL, OUTGOING_NUMBER, campaign_name, WORK_TITLE, Вид_работ, DIRECTOR_NAME, PRICE_TOTAL, MUN_R_SCOPE_FRAGMENT, DOCUMENT_ID). "
    "canonical — каноническое имя из списка системных переменных. "
    "recipient — переменные, которые нужно сопоставить с колонками Excel получателей. "
    "unknown — если не уверен."
)

_NORMALIZE_PROMPT = (
    "Ты нормализуешь плейсхолдеры в шаблонах документов/писем. "
    'Верни только JSON: {"variables":[{"token":"...","canonical":"...","kind":"recipient|system|compound|unknown"}]} '
    "token — как найдено в тексте (bare ADM_NAME или {{company}}). "
    "canonical — стандартное имя: ADM_NAME, DATE, MUN_R_SCOPE_FRAGMENT, company и т.д. "
    "kind=system для DATE/current_date/VALID_UNTIL/OUTGOING_NUMBER/campaign_name/WORK_TITLE/Вид_работ/DIRECTOR_NAME/DOCUMENT_ID/ид/id/номер."
)


def default_model() -> str:
    models = list_models()
    return str(models[0]["id"]) if models else "gpt-4o-mini"


def _heuristic_system_variable(name: str) -> str | None:
    from src.campaigns.placeholder_semantic import resolve_system_canonical

    canonical = resolve_system_canonical(name)
    if canonical and canonical in SYSTEM_AUTO_VARIABLES:
        return canonical
    return None


def classify_system_variables(
    template_variables: list[dict[str, Any]],
    *,
    model: str = "",
) -> dict[str, Any]:
    pending = [
        str(item.get("name") or "")
        for item in template_variables
        if str(item.get("name") or "")
    ]
    system_resolved: dict[str, str] = {}
    recipient_pending: list[str] = []
    unknown: list[str] = []

    for name in pending:
        canonical = _heuristic_system_variable(name)
        if canonical:
            system_resolved[name] = canonical
        else:
            recipient_pending.append(name)

    if not recipient_pending:
        return {"system_resolved": system_resolved, "recipient": [], "unknown": []}

    var_lines = "\n".join(f"- {name}" for name in recipient_pending)
    try:
        payload = _call_llm(model or default_model(), _SYSTEM_CLASSIFY_PROMPT, f"Переменные:\n{var_lines}")
    except RuntimeError:
        return {"system_resolved": system_resolved, "recipient": recipient_pending, "unknown": []}

    for item in payload.get("system_resolved") or []:
        if not isinstance(item, dict):
            continue
        template_variable = str(item.get("template_variable") or "").strip()
        canonical = str(item.get("canonical") or "").strip()
        if template_variable and canonical:
            system_resolved[template_variable] = canonical
            if template_variable in recipient_pending:
                recipient_pending.remove(template_variable)

    for name in payload.get("recipient") or []:
        safe = str(name or "").strip()
        if safe and safe not in recipient_pending and safe not in system_resolved:
            recipient_pending.append(safe)

    for name in payload.get("unknown") or []:
        safe = str(name or "").strip()
        if safe and safe not in unknown:
            unknown.append(safe)

    return {
        "system_resolved": system_resolved,
        "recipient": recipient_pending,
        "unknown": unknown,
    }


def normalize_placeholders(
    text: str,
    *,
    model: str = "",
) -> list[dict[str, Any]]:
    placeholders = discover_placeholders(text)
    if not placeholders:
        return []

    normalized: list[dict[str, Any]] = []
    pending_ai: list[PlaceholderInfo] = []
    for item in placeholders:
        canonical = _heuristic_system_variable(item.name)
        if canonical:
            normalized.append(
                {
                    "token": item.token,
                    "name": canonical,
                    "label": canonical.replace("_", " ").title(),
                    "source": "system",
                    "kind": item.kind,
                }
            )
            continue
        if item.kind == "brace" and item.name in {"company", "contact_name", "email", "region"}:
            normalized.append(
                {
                    "token": item.token,
                    "name": item.name,
                    "label": item.name,
                    "source": "recipient",
                    "kind": item.kind,
                }
            )
            continue
        if item.kind in {"bare", "compound"} or item.name.isupper():
            pending_ai.append(item)
        else:
            normalized.append(
                {
                    "token": item.token,
                    "name": item.name,
                    "label": item.name,
                    "source": "recipient",
                    "kind": item.kind,
                }
            )

    if pending_ai:
        lines = "\n".join(f"- {item.token} ({item.kind})" for item in pending_ai)
        try:
            payload = _call_llm(model or default_model(), _NORMALIZE_PROMPT, f"Плейсхолдеры:\n{lines}")
            ai_by_token = {
                str(row.get("token") or ""): row
                for row in (payload.get("variables") or [])
                if isinstance(row, dict)
            }
            for item in pending_ai:
                row = ai_by_token.get(item.token) or {}
                canonical = str(row.get("canonical") or item.name).strip() or item.name
                kind = str(row.get("kind") or "").strip().lower()
                source = "system" if kind == "system" or _heuristic_system_variable(canonical) else "recipient"
                normalized.append(
                    {
                        "token": item.token,
                        "name": canonical,
                        "label": canonical.replace("_", " ").title(),
                        "source": source,
                        "kind": item.kind,
                    }
                )
        except RuntimeError:
            for item in pending_ai:
                normalized.append(
                    {
                        "token": item.token,
                        "name": item.name,
                        "label": item.name.replace("_", " "),
                        "source": "recipient",
                        "kind": item.kind,
                    }
                )

    deduped: dict[str, dict[str, Any]] = {}
    for item in normalized:
        deduped[str(item["name"])] = item
    return list(deduped.values())
