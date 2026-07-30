"""Full rendered template review for campaign email/document templates."""

from __future__ import annotations

import re
from typing import Any

from src.campaigns.substitution_context import build_substitution_context
from src.campaigns.substitution_engine import (
    discover_placeholders,
    find_template_defects,
    html_to_review_text,
    is_blocking_placeholder_defect,
)
from src.campaigns.text_local_review import review_email_text
from src.campaigns.variable_match_service import render_template_text
from src.infra.models import Campaign, CampaignRecipient

_LANGUAGE_KINDS = frozenset({"punctuation", "grammar", "case"})
_CASE_FIELD_ALIASES: dict[str, frozenset[str]] = {
    "HEAD_FIO_1": frozenset({"HEAD_FIO", "HEAD_FIO_1"}),
    "HEAD_FIO_2": frozenset({"HEAD_FIO", "HEAD_FIO_2"}),
    "MUN_NAME_1": frozenset({"MUN_NAME", "MUN_NAME_1"}),
    "MUN_NAME_2": frozenset({"MUN_NAME", "MUN_NAME_2"}),
    "ADM_NAME_1": frozenset({"ADM", "ADM_NAME", "ADM_NAME_1"}),
}


def _normalize_issue_severity(kind: str, severity: str) -> str:
    if kind in _LANGUAGE_KINDS and severity == "error":
        return "warning"
    return severity


def _issue_dict(
    *,
    template_id: str | None,
    template_name: str,
    field: str,
    kind: str,
    severity: str,
    fragment: str,
    message: str,
    suggestion: str = "",
    source: str = "local",
    blocking: bool | None = None,
) -> dict[str, Any]:
    severity = _normalize_issue_severity(kind, severity)
    issue = {
        "template_id": template_id,
        "template_name": template_name,
        "field": field,
        "kind": kind,
        "severity": severity,
        "fragment": fragment,
        "message": message,
        "suggestion": suggestion,
        "token": fragment,
        "source": source,
    }
    if blocking is not None:
        issue["blocking"] = blocking
    return issue


def _template_case_fields(template_text: str) -> set[str]:
    placeholder_names = {
        str(item.name or "").strip().upper()
        for item in discover_placeholders(template_text or "")
    }
    if not placeholder_names:
        placeholder_names = {
            token.upper()
            for token in re.findall(r"\b[A-Z][A-Z0-9_]+\b", template_text or "")
        }
    return {
        field
        for field, aliases in _CASE_FIELD_ALIASES.items()
        if placeholder_names.intersection(aliases)
    }


def _case_values_equivalent(first: str, second: str) -> bool:
    def normalize(value: str) -> str:
        text = str(value or "").translate(
            str.maketrans(
                {
                    "\u00ab": '"',
                    "\u00bb": '"',
                    "\u201c": '"',
                    "\u201d": '"',
                }
            )
        )
        text = re.sub(r"\s+", " ", text).strip()
        return text.casefold()

    return normalize(first) == normalize(second)


def _artifact_message(kind: str, token: str) -> str:
    if kind == "malformed":
        return f"Некорректный синтаксис плейсхолдера {token}"
    if kind == "artifact":
        return f"В тексте остался артефакт шаблона {token} — замените на системную переменную, например {{WORK_TITLE}}"
    return f"Не заполнена переменная {token}"


def _append_placeholder_issues(
    issues: list[dict[str, Any]],
    *,
    template_id: str | None,
    template_name: str,
    field: str,
    rendered_text: str,
    source: str = "rendered",
) -> None:
    if not rendered_text:
        return
    seen: set[tuple[str, str, str]] = set()
    for defect in find_template_defects(rendered_text, source=source):
        if not is_blocking_placeholder_defect(defect):
            continue
        key = (field, defect.kind, defect.token)
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            _issue_dict(
                template_id=template_id,
                template_name=template_name,
                field=field,
                kind=defect.kind,
                severity="error",
                fragment=defect.token,
                message=_artifact_message(defect.kind, defect.token),
                source="placeholder",
                blocking=True,
            )
        )


def _append_strict_original_issues(
    issues: list[dict[str, Any]],
    *,
    template_id: str | None,
    template_name: str,
    subject_template: str,
    body_html_template: str,
    body_text_template: str,
) -> None:
    for field, source_text in (
        ("subject", subject_template or ""),
        ("body_html", body_html_template or ""),
        ("body_text", body_text_template or ""),
    ):
        if not source_text.strip():
            continue
        _append_placeholder_issues(
            issues,
            template_id=template_id,
            template_name=template_name,
            field=field,
            rendered_text=source_text,
            source="original",
        )


def review_document_text_for_placeholders(
    text: str,
    *,
    template_id: str | None,
    template_name: str,
    field: str = "attachment",
) -> list[dict[str, Any]]:
    return review_document_text(
        text,
        template_id=template_id,
        template_name=template_name,
        field=field,
        include_language=False,
    )


def review_document_text(
    text: str,
    *,
    template_id: str | None,
    template_name: str,
    field: str = "attachment",
    include_language: bool = True,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    _append_placeholder_issues(
        issues,
        template_id=template_id,
        template_name=template_name,
        field=field,
        rendered_text=text,
    )
    if include_language:
        _append_local_language_issues(
            issues,
            template_id=template_id,
            template_name=template_name,
            field=field,
            rendered_text=text,
        )
    return issues


def _append_local_language_issues(
    issues: list[dict[str, Any]],
    *,
    template_id: str | None,
    template_name: str,
    field: str,
    rendered_text: str,
) -> None:
    plain = html_to_review_text(rendered_text) if rendered_text else ""
    if not plain:
        return
    for item in review_email_text(
        plain,
        field=field,
        check_terminal_punctuation=field != "subject",
    ):
        issues.append(
            _issue_dict(
                template_id=template_id,
                template_name=template_name,
                field=field,
                kind=item.kind,
                severity=item.severity,
                fragment=item.fragment,
                message=item.message,
                suggestion=item.suggestion,
            )
        )


def _append_case_issues(
    issues: list[dict[str, Any]],
    *,
    template_id: str | None,
    template_name: str,
    recipient: CampaignRecipient,
    campaign: Campaign,
    template_text: str,
) -> None:
    from src.campaigns.substitution_context import recipient_row
    from src.generator.generation.config_generator import ENABLE_CASE_AGENT
    from src.generator.generation.transforms import build_document_context
    from src.generator.inflection.ai_case_agent import run_case_validation_agent

    if not ENABLE_CASE_AGENT:
        return
    relevant_fields = _template_case_fields(template_text)
    if not relevant_fields:
        return

    row = recipient_row(recipient)
    context = build_document_context(row, outgoing_number=recipient.row_index or 1, work_type=campaign.work_type or None)
    result = run_case_validation_agent(row, context)
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip()
        if status == "ok":
            continue
        field_name = str(item.get("field") or "context")
        if field_name not in relevant_fields:
            continue
        comment = str(item.get("comment") or "Возможная ошибка падежа").strip()
        corrected = str(item.get("corrected_value") or "").strip()
        generated = str(item.get("generated_value") or "").strip()
        if (
            status == "fix"
            and _case_values_equivalent(corrected, str(context.get(field_name) or ""))
        ):
            continue
        fragment = generated or corrected or field_name
        issues.append(
            _issue_dict(
                template_id=template_id,
                template_name=template_name,
                field="context",
                kind="case",
                severity="warning",
                fragment=fragment,
                message=comment,
                suggestion=corrected,
                source="case_agent",
                blocking=False,
            )
        )


def _append_ai_issues(
    issues: list[dict[str, Any]],
    *,
    template_id: str | None,
    template_name: str,
    blocks: list[tuple[str, str]],
) -> None:
    from src.generator.generation.config_generator import ENABLE_EMAIL_LANGUAGE_AI

    if not ENABLE_EMAIL_LANGUAGE_AI:
        return

    try:
        from src.generator.philologist.document_review_agent import _run_ai_review
    except ImportError:
        return

    ai_items = _run_ai_review(blocks, ai_enabled=True)
    for item in ai_items:
        severity = str(item.severity or "warning")
        if severity not in {"error", "warning", "info"}:
            severity = "warning"
        if severity == "error":
            severity = "warning"
        issues.append(
            _issue_dict(
                template_id=template_id,
                template_name=template_name,
                field=str(item.location or "body"),
                kind="grammar",
                severity=severity,
                fragment=str(item.fragment or ""),
                message=str(item.issue or "Грамматическая ошибка"),
                suggestion=str(item.suggestion or ""),
                source="ai",
                blocking=False,
            )
        )


def review_rendered_template(
    *,
    template_id: str | None,
    template_name: str,
    subject_template: str,
    body_html_template: str,
    body_text_template: str,
    recipient: CampaignRecipient,
    campaign: Campaign,
    deep: bool = False,
    advisory: bool = False,
    rendered_subject: str | None = None,
    rendered_html: str | None = None,
    rendered_text: str | None = None,
    include_placeholder_issues: bool = False,
    strict_preview: bool = False,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    template_text = "\n".join([subject_template or "", body_html_template or "", body_text_template or ""])
    if rendered_subject is None:
        rendered_subject = render_template_text(
            subject_template or "",
            recipient=recipient,
            campaign=campaign,
            template_id=template_id,
            template_name=template_name,
            template_text=template_text,
        )
    if rendered_html is None:
        rendered_html = render_template_text(
            body_html_template or "",
            recipient=recipient,
            campaign=campaign,
            template_id=template_id,
            template_name=template_name,
            template_text=template_text,
        )
    if rendered_text is None:
        rendered_text = (
            render_template_text(
                body_text_template or "",
                recipient=recipient,
                campaign=campaign,
                template_id=template_id,
                template_name=template_name,
                template_text=template_text,
            )
            if (body_text_template or "").strip()
            else html_to_review_text(rendered_html)
        )

    if include_placeholder_issues:
        if strict_preview:
            _append_strict_original_issues(
                issues,
                template_id=template_id,
                template_name=template_name,
                subject_template=subject_template or "",
                body_html_template=body_html_template or "",
                body_text_template=body_text_template or "",
            )
        _append_placeholder_issues(
            issues,
            template_id=template_id,
            template_name=template_name,
            field="subject",
            rendered_text=rendered_subject,
        )
        _append_placeholder_issues(
            issues,
            template_id=template_id,
            template_name=template_name,
            field="body_html",
            rendered_text=rendered_html,
        )
        if body_text_template:
            _append_placeholder_issues(
                issues,
                template_id=template_id,
                template_name=template_name,
                field="body_text",
                rendered_text=rendered_text,
            )

    _append_local_language_issues(
        issues,
        template_id=template_id,
        template_name=template_name,
        field="subject",
        rendered_text=rendered_subject,
    )
    _append_local_language_issues(
        issues,
        template_id=template_id,
        template_name=template_name,
        field="body_html",
        rendered_text=rendered_html,
    )

    if advisory or deep:
        _append_case_issues(
            issues,
            template_id=template_id,
            template_name=template_name,
            recipient=recipient,
            campaign=campaign,
            template_text=template_text,
        )
        blocks: list[tuple[str, str]] = []
        if rendered_subject.strip():
            blocks.append(("subject", html_to_review_text(rendered_subject)))
        body_plain = html_to_review_text(rendered_html)
        if body_plain.strip():
            for index, paragraph in enumerate([part.strip() for part in body_plain.split(". ") if part.strip()], 1):
                blocks.append((f"paragraph:{index}", paragraph))
        _append_ai_issues(
            issues,
            template_id=template_id,
            template_name=template_name,
            blocks=blocks,
        )

    return issues


def _promote_deep_blocking_issue(issue: dict[str, Any]) -> None:
    kind = str(issue.get("kind") or "")
    suggestion = str(issue.get("suggestion") or "").strip()
    source = str(issue.get("source") or "local")
    if issue.get("blocking") is True:
        issue["severity"] = "error"
        return

    if source in {"ai", "case_agent"}:
        if kind in _LANGUAGE_KINDS:
            issue["severity"] = "warning"
            issue["blocking"] = False
        return

    if kind in {"grammar", "case"} or (kind == "punctuation" and suggestion):
        issue["severity"] = "error"
        issue["blocking"] = True


def review_campaign_templates(
    campaign: Campaign,
    *,
    deep: bool = False,
    advisory: bool = False,
    include_placeholder_issues: bool = False,
    strict_preview: bool = False,
) -> list[dict[str, Any]]:
    from src.campaigns.variable_match_service import _collect_templates_for_validation, _validation_recipients

    recipients = _validation_recipients(campaign)
    if not recipients:
        return []

    all_issues: list[dict[str, Any]] = []
    for template_info in _collect_templates_for_validation(campaign):
        template_id = str(template_info.get("template_id") or "") or None
        template_name = str(template_info.get("template_name") or "шаблон")
        subject = str(template_info.get("subject") or "")
        body_html = str(template_info.get("body_html") or "")
        body_text = str(template_info.get("body_text") or "")
        combined = str(template_info.get("text") or "")
        if not body_html and combined:
            body_html = combined

        template_text = "\n".join([subject, body_html, body_text])
        seen_rendered: set[tuple[str, str, str]] = set()
        advisory_done = False
        strict_preview_done = False
        for recipient in recipients:
            rendered_subject = render_template_text(
                subject,
                recipient=recipient,
                campaign=campaign,
                template_id=template_id,
                template_name=template_name,
                template_text=template_text,
            )
            rendered_html = render_template_text(
                body_html,
                recipient=recipient,
                campaign=campaign,
                template_id=template_id,
                template_name=template_name,
                template_text=template_text,
            )
            rendered_text = (
                render_template_text(
                    body_text,
                    recipient=recipient,
                    campaign=campaign,
                    template_id=template_id,
                    template_name=template_name,
                    template_text=template_text,
                )
                if body_text.strip()
                else html_to_review_text(rendered_html)
            )
            rendered_signature = (rendered_subject, rendered_html, rendered_text)
            if rendered_signature in seen_rendered:
                continue
            seen_rendered.add(rendered_signature)

            run_deep = bool(deep and not advisory_done)
            run_advisory = bool(advisory and not advisory_done)
            rendered_issues = review_rendered_template(
                template_id=template_id,
                template_name=template_name,
                subject_template=subject,
                body_html_template=body_html,
                body_text_template=body_text,
                recipient=recipient,
                campaign=campaign,
                deep=run_deep,
                advisory=run_advisory,
                rendered_subject=rendered_subject,
                rendered_html=rendered_html,
                rendered_text=rendered_text,
                include_placeholder_issues=include_placeholder_issues,
                strict_preview=strict_preview and not strict_preview_done,
            )
            strict_preview_done = True
            if run_deep or run_advisory:
                advisory_done = True
            for issue in rendered_issues:
                if deep:
                    _promote_deep_blocking_issue(issue)
                issue.setdefault("recipient_id", str(recipient.id))
                issue.setdefault("recipient_row_index", int(recipient.row_index or 0))
            all_issues.extend(rendered_issues)
    return all_issues


def partition_review_messages(issues: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for issue in issues:
        template_name = str(issue.get("template_name") or "шаблон")
        message = str(issue.get("message") or issue.get("token") or "Ошибка шаблона")
        line = f"Шаблон «{template_name}»: {message}"
        severity = str(issue.get("severity") or "error")
        if severity == "error":
            errors.append(line)
        else:
            warnings.append(line)
    return errors, warnings
