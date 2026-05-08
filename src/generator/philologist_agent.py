from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from docx import Document

from src.generator.agent_handoff import (
    append_agent_event,
    count_tasks_for_agent,
    create_task,
    get_recent_events,
    get_tasks_for_agent,
    mark_tasks_in_progress,
    set_task_statuses,
)
from src.generator.ai_case_agent import (
    OpenAI,
    _extract_json_payload,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.generator.config_generator import DOCUMENT_REVIEW_MODEL, OUTPUT_DIR
from src.generator.document_review_agent import review_docx
from src.generator.philology_knowledge import find_relevant_rules, format_rules_context
from src.generator.pdf_converter import convert_docx_batch
from src.generator.responsibility_matrix import diagnose_responsibility
from src.jobs import load_agent_state, save_agent_state
from src.jobs.storage import resolve_job_paths
from src.utils.config import settings

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None


PHILOLOGIST_STATE: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "total_documents": 0,
    "processed_documents": 0,
    "fixed_documents": 0,
    "documents_with_issues": 0,
    "documents": [],
    "summary_text": "Агент-филолог ещё не запускался.",
    "task_stats": {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0},
    "tasks": [],
    "recent_events": [],
}


def _load_philologist_state(job_id: str | None = None) -> dict[str, Any]:
    return load_agent_state("philologist", PHILOLOGIST_STATE, job_id)


def _save_philologist_state(state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    return save_agent_state("philologist", state, job_id)


def _build_llm_client():
    if not OpenAI:
        return None
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url
    if httpx:
        client_kwargs["http_client"] = httpx.Client(
            http2=False,
            timeout=httpx.Timeout(connect=10, read=90, write=90, pool=90),
            trust_env=False,
        )
    return OpenAI(**client_kwargs)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _iter_document_paragraphs(doc: Document) -> Iterable[tuple[str, Any]]:
    for index, paragraph in enumerate(doc.paragraphs, 1):
        yield (f"paragraph:{index}", paragraph)

    for table_index, table in enumerate(doc.tables, 1):
        for row_index, row in enumerate(table.rows, 1):
            for cell_index, cell in enumerate(row.cells, 1):
                for paragraph_index, paragraph in enumerate(cell.paragraphs, 1):
                    yield (
                        f"table:{table_index}:row:{row_index}:cell:{cell_index}:paragraph:{paragraph_index}",
                        paragraph,
                    )


def _replace_paragraph_text(paragraph, new_text: str) -> bool:
    current_text = "".join(run.text for run in paragraph.runs) if paragraph.runs else paragraph.text
    if current_text == new_text:
        return False

    if paragraph.runs:
        paragraph.runs[0].text = new_text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(new_text)
    return True


def _replace_fragment_in_paragraph(paragraph, fragment: str, replacement: str) -> bool:
    current_text = "".join(run.text for run in paragraph.runs) if paragraph.runs else paragraph.text
    if not fragment or not replacement or fragment not in current_text:
        return False
    new_text = current_text.replace(fragment, replacement, 1)
    if new_text == current_text:
        return False
    return _replace_paragraph_text(paragraph, new_text)


def _normalize_double_spaces(text: str) -> str:
    while "  " in text:
        text = text.replace("  ", " ")
    return text


EDITORIAL_SUGGESTION_PREFIXES = (
    "заменить ",
    "исправить ",
    "нужно ",
    "следует ",
    "проверить ",
    "убрать ",
)


def _looks_like_editorial_instruction(text: str) -> bool:
    normalized = _safe_text(text).strip().lower()
    if not normalized:
        return False
    if any(normalized.startswith(prefix) for prefix in EDITORIAL_SUGGESTION_PREFIXES):
        return True
    if "заменить" in normalized and (" на " in normalized or '"' in normalized or "«" in normalized):
        return True
    return False


def _apply_issue_to_document(location_map: dict[str, Any], issue: dict[str, Any]) -> bool:
    location = _safe_text(issue.get("location"))
    paragraph = location_map.get(location)
    if paragraph is None:
        return False

    issue_text = _safe_text(issue.get("issue")).lower()
    fragment = _safe_text(issue.get("fragment"))
    suggestion = _safe_text(issue.get("suggestion"))
    current_text = paragraph.text

    if _looks_like_editorial_instruction(suggestion):
        return False

    if "двойные пробелы" in issue_text:
        normalized = _normalize_double_spaces(current_text)
        return _replace_paragraph_text(paragraph, normalized)

    if "после числа используется неверная форма слова" in issue_text and suggestion:
        return _replace_paragraph_text(paragraph, suggestion)

    if issue.get("source") == "local" and suggestion and fragment:
        if fragment in current_text:
            return _replace_fragment_in_paragraph(paragraph, fragment, suggestion)
        if current_text.strip() == fragment.strip():
            return _replace_paragraph_text(paragraph, suggestion)

    if issue.get("source") == "ai" and suggestion and fragment:
        if fragment in current_text:
            return _replace_fragment_in_paragraph(paragraph, fragment, suggestion)
        if current_text.strip() == fragment.strip():
            return _replace_paragraph_text(paragraph, suggestion)

    return False


def _auto_fix_docx(docx_path: Path, review_result: dict[str, Any]) -> dict[str, Any]:
    doc = Document(docx_path)
    location_map = {location: paragraph for location, paragraph in _iter_document_paragraphs(doc)}
    applied_fixes: list[dict[str, str]] = []

    for issue in review_result.get("issues", []):
        if not isinstance(issue, dict):
            continue
        if _apply_issue_to_document(location_map, issue):
            applied_fixes.append(
                {
                    "location": _safe_text(issue.get("location")),
                    "issue": _safe_text(issue.get("issue")),
                    "suggestion": _safe_text(issue.get("suggestion")),
                }
            )

    if applied_fixes:
        doc.save(docx_path)

    return {
        "applied_fix_count": len(applied_fixes),
        "applied_fixes": applied_fixes,
    }


def _rebuild_pdf_for_docx(docx_path: Path) -> str | None:
    pdf_map = convert_docx_batch([docx_path], docx_path.parent, chunk_size=1, worker_count=1)
    pdf_path = pdf_map.get(docx_path)
    return str(pdf_path) if pdf_path and pdf_path.exists() else None


def _format_summary(documents: list[dict[str, Any]]) -> str:
    total = len(documents)
    with_issues = sum(1 for item in documents if item.get("issue_count", 0) > 0)
    fixed = sum(1 for item in documents if item.get("applied_fix_count", 0) > 0)
    if total == 0:
        return "Готовые документы для проверки пока не найдены."
    if with_issues == 0:
        return f"Проверил {total} документов. Явных языковых ошибок не нашёл."
    return (
        f"Проверил {total} документов. "
        f"Замечания нашёл в {with_issues}, автоматически исправил {fixed}. "
        "Если хочешь, могу показать, что именно исправил и где остались спорные места."
    )


def _format_run_summary(documents: list[dict[str, Any]], *, sender_handoffs: int = 0) -> str:
    base = _format_summary(documents)
    if sender_handoffs > 0:
        return base + f" Передал отправщику задач на дополнительную проверку перед отправкой: {sender_handoffs}."
    return base


def _format_fixed_details(documents: list[dict[str, Any]], limit: int = 5) -> str:
    lines: list[str] = []
    for item in documents:
        fixes = item.get("applied_fixes") or []
        if not fixes:
            continue
        lines.append(f"{item['name']}:")
        for fix in fixes[:2]:
            issue = _safe_text(fix.get("issue")) or "языковая правка"
            suggestion = _safe_text(fix.get("suggestion"))
            details = issue if not suggestion else f"{issue} -> {suggestion}"
            lines.append(f"- {details}")
        if len(lines) >= limit * 2:
            break
    if not lines:
        return "Автоматических исправлений пока не было. Я только нашёл замечания."
    return "Вот что я исправил:\n" + "\n".join(lines[: limit * 2])


def _format_issue_details(documents: list[dict[str, Any]], limit: int = 5) -> str:
    lines: list[str] = []
    for item in documents:
        issues = item.get("issues") or []
        if not issues:
            continue
        lines.append(f"{item['name']}:")
        for issue in issues[:2]:
            issue_text = _safe_text(issue.get("issue")) or "есть замечание"
            suggestion = _safe_text(issue.get("suggestion"))
            details = issue_text if not suggestion else f"{issue_text} -> {suggestion}"
            lines.append(f"- {details}")
        if len(lines) >= limit * 2:
            break
    if not lines:
        return "Явных языковых проблем в проверенных документах я не нашёл."
    return "Вот где есть замечания:\n" + "\n".join(lines[: limit * 2])


def _format_rule_details(message: str) -> str:
    rules = find_relevant_rules(message, limit=4)
    if not rules:
        return "В локальной базе правил я не нашёл точного совпадения по этому вопросу. Могу всё равно проверить формулировку по найденным замечаниям."
    lines = ["Вот на какие правила я могу опереться:"]
    for rule in rules:
        lines.append(
            f"- {_safe_text(rule.get('title'))}: {_safe_text(rule.get('rule'))} "
            f"(источник: {_safe_text(rule.get('source'))})"
        )
    return "\n".join(lines)


def _extract_row_id_from_docx_path(docx_path: Path) -> str:
    folder_name = docx_path.parent.name
    return folder_name.split("_", 1)[0].strip()


def _extract_mun_name_from_docx_path(docx_path: Path) -> str:
    folder_name = docx_path.parent.name
    if "_" not in folder_name:
        return folder_name
    return folder_name.split("_", 1)[1].strip()


def run_philologist(
    *,
    output_dir: Path | None = None,
    ai_enabled: bool = True,
    row_ids: list[str] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    job_paths = resolve_job_paths(job_id)
    target_dir = output_dir or (job_paths.output_dir if not job_paths.uses_legacy_layout else OUTPUT_DIR)
    docx_paths = sorted(target_dir.rglob("*.docx"))
    requested_row_ids = {str(item).strip() for item in (row_ids or []) if str(item).strip()}
    if requested_row_ids:
        docx_paths = [
            path for path in docx_paths
            if _extract_row_id_from_docx_path(path) in requested_row_ids
        ]
    claimed_tasks = mark_tasks_in_progress(
        "philologist",
        row_ids=sorted(requested_row_ids) if requested_row_ids else None,
        job_id=job_id,
    )

    state = _load_philologist_state(job_id)
    state.update(
        {
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "total_documents": len(docx_paths),
            "processed_documents": 0,
            "fixed_documents": 0,
            "documents_with_issues": 0,
            "documents": [],
            "summary_text": (
                "Агент-филолог начал проверку документов."
                if not claimed_tasks
                else f"Агент-филолог начал проверку документов и принял {len(claimed_tasks)} внутренних задач."
            ),
            "task_stats": count_tasks_for_agent("philologist", job_id),
            "tasks": get_tasks_for_agent("philologist", job_id)[:20],
            "recent_events": get_recent_events(agent_name="philologist", limit=20, job_id=job_id),
        }
    )
    _save_philologist_state(state, job_id)

    started_at = perf_counter()
    processed_documents: list[dict[str, Any]] = []
    for index, docx_path in enumerate(docx_paths, start=1):
        review_result = review_docx(docx_path, ai_enabled=ai_enabled)
        fix_result = _auto_fix_docx(docx_path, review_result)
        pdf_path = _rebuild_pdf_for_docx(docx_path) if fix_result["applied_fix_count"] > 0 else None

        document_entry = {
            "index": index,
            "name": docx_path.name,
            "path": str(docx_path),
            "folder": str(docx_path.parent),
            "row_id": _extract_row_id_from_docx_path(docx_path),
            "mun_name": _extract_mun_name_from_docx_path(docx_path),
            "issue_count": int(review_result.get("issue_count", 0)),
            "local_issue_count": int(review_result.get("local_issue_count", 0)),
            "ai_issue_count": int(review_result.get("ai_issue_count", 0)),
            "ai_error": review_result.get("ai_error"),
            "issues": review_result.get("issues", []),
            "applied_fix_count": fix_result["applied_fix_count"],
            "applied_fixes": fix_result["applied_fixes"],
            "updated_pdf": pdf_path,
        }
        processed_documents.append(document_entry)

        state["processed_documents"] = index
        state["documents"] = processed_documents
        state["fixed_documents"] = sum(1 for item in processed_documents if item.get("applied_fix_count", 0) > 0)
        state["documents_with_issues"] = sum(1 for item in processed_documents if item.get("issue_count", 0) > 0)
        state["summary_text"] = _format_summary(processed_documents)
        _save_philologist_state(state, job_id)

    row_rollups: dict[str, dict[str, Any]] = {}
    for item in processed_documents:
        row_id = _safe_text(item.get("row_id"))
        if not row_id:
            continue
        row_entry = row_rollups.setdefault(
            row_id,
            {
                "row_id": row_id,
                "mun_name": _safe_text(item.get("mun_name")),
                "issue_count": 0,
                "applied_fix_count": 0,
            },
        )
        row_entry["issue_count"] += int(item.get("issue_count", 0))
        row_entry["applied_fix_count"] += int(item.get("applied_fix_count", 0))

    sender_handoffs = 0
    for row_entry in row_rollups.values():
        row_id = row_entry["row_id"]
        mun_name = row_entry["mun_name"]
        issue_count = int(row_entry["issue_count"])
        applied_fix_count = int(row_entry["applied_fix_count"])
        unresolved_issue_count = max(0, issue_count - applied_fix_count)
        note = (
            "Филолог завершил проверку документов."
            if unresolved_issue_count == 0
            else f"Филолог нашёл {unresolved_issue_count} нерешённых замечаний."
        )
        set_task_statuses(
            "philologist",
            row_id=row_id,
            task_type="review_generated_documents",
            new_status="done",
            note=note,
            resolution_summary="Филолог завершил проверку документов по строке.",
            job_id=job_id,
        )
        if not settings.inter_agent_handoffs_enabled:
            continue
        if unresolved_issue_count > 0:
            diagnosis = diagnose_responsibility(
                symptom="philology_review_block",
                context={"unresolved_issue_count": unresolved_issue_count},
            )
            create_task(
                source_agent="philologist",
                target_agent=diagnosis["owner_agent"],
                owner_agent=diagnosis["owner_agent"],
                task_type="review_before_send",
                problem_type=diagnosis["problem_type"],
                symptom="philology_review_block",
                root_cause=diagnosis["root_cause"],
                priority=diagnosis["priority"],
                blocking=diagnosis["blocking"],
                can_retry_after=diagnosis["can_retry_after"],
                row_id=row_id,
                mun_name=mun_name,
                details={
                    "issue_count": issue_count,
                    "applied_fix_count": applied_fix_count,
                    "unresolved_issue_count": unresolved_issue_count,
                    "reason": "Перед отправкой нужно учесть замечания филолога.",
                },
                job_id=job_id,
            )
            sender_handoffs += 1
            append_agent_event(
                source_agent="philologist",
                target_agent="sender",
                event_type="review_flagged",
                message=f"Филолог нашёл замечания по комплекту документов для строки {row_id}.",
                row_id=row_id,
                mun_name=mun_name,
                details={
                    "issue_count": issue_count,
                    "unresolved_issue_count": unresolved_issue_count,
                },
                job_id=job_id,
            )
        else:
            create_task(
                source_agent="philologist",
                target_agent="sender",
                owner_agent="sender",
                task_type="resume_send_readiness",
                problem_type="delivery_blocked",
                symptom="documents_ready_after_review",
                root_cause="Филолог завершил проверку и снял языковой блокер, можно повторно проверить готовность к отправке.",
                priority="medium",
                blocking=False,
                can_retry_after=True,
                row_id=row_id,
                mun_name=mun_name,
                details={
                    "issue_count": issue_count,
                    "applied_fix_count": applied_fix_count,
                    "reason": "После филологической проверки строку можно снова прогнать через отправщика.",
                },
                job_id=job_id,
            )
            set_task_statuses(
                "sender",
                row_id=row_id,
                task_type="review_before_send",
                new_status="done",
                note="Филолог подтвердил, что критичных замечаний перед отправкой не осталось.",
                resolution_summary="Критичных замечаний перед отправкой не осталось.",
                job_id=job_id,
            )
            append_agent_event(
                source_agent="philologist",
                target_agent="sender",
                event_type="review_completed",
                message=f"Филолог проверил документы для строки {row_id}; критичных замечаний не осталось.",
                row_id=row_id,
                mun_name=mun_name,
                details={
                    "issue_count": issue_count,
                    "applied_fix_count": applied_fix_count,
                },
                job_id=job_id,
            )

    state["status"] = "completed"
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
    state["task_stats"] = count_tasks_for_agent("philologist", job_id)
    state["tasks"] = get_tasks_for_agent("philologist", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="philologist", limit=20, job_id=job_id)
    state["summary_text"] = _format_run_summary(processed_documents, sender_handoffs=sender_handoffs)
    _save_philologist_state(state, job_id)
    return dict(state)


def get_philologist_status(job_id: str | None = None) -> dict[str, Any]:
    state = _load_philologist_state(job_id)
    job_paths = resolve_job_paths(job_id)
    target_dir = job_paths.output_dir if not job_paths.uses_legacy_layout else OUTPUT_DIR
    state["task_stats"] = count_tasks_for_agent("philologist", job_id)
    state["tasks"] = get_tasks_for_agent("philologist", job_id)[:20]
    state["recent_events"] = get_recent_events(agent_name="philologist", limit=20, job_id=job_id)
    if state.get("status") == "idle":
        state["total_documents"] = len(list(target_dir.rglob("*.docx"))) if target_dir.exists() else 0
    return state


def _fallback_chat_answer(message: str, state: dict[str, Any]) -> str:
    documents = state.get("documents") or []
    if not documents:
        return (
            "Агент-филолог ещё не запускался. "
            f"{_format_rule_details(message)}"
        )
    issues = [item for item in documents if item.get("issue_count", 0) > 0]
    fixed = [item for item in documents if item.get("applied_fix_count", 0) > 0]
    parts = [state.get("summary_text") or "Проверка завершена, но краткая сводка пока недоступна."]
    if issues:
        parts.append(_format_issue_details(issues, limit=3))
    if fixed:
        parts.append(_format_fixed_details(fixed, limit=3))
    parts.append(_format_rule_details(message))
    return "\n\n".join(part for part in parts if part)


def chat_with_philologist(message: str, *, job_id: str | None = None) -> dict[str, Any]:
    state = get_philologist_status(job_id)
    client = _build_llm_client()
    relevant_rules = find_relevant_rules(message, limit=4)
    rules_context = format_rules_context(relevant_rules)
    if not client:
        return {"reply": _fallback_chat_answer(message, state), "state": state}

    compact_documents = []
    for item in (state.get("documents") or [])[:20]:
        compact_documents.append(
            {
                "name": item.get("name"),
                "issue_count": item.get("issue_count"),
                "applied_fix_count": item.get("applied_fix_count"),
                "ai_error": item.get("ai_error"),
                "issues": (item.get("issues") or [])[:5],
            }
        )

    prompt = (
        "Ты агент-филолог для проверки коммерческих предложений и договоров. "
        "Ты уже проверил документы и должен отвечать пользователю по результатам этой проверки. "
        "Отвечай по-русски, кратко и по делу. "
        "Если пользователь спрашивает об ошибках или исправлениях, сначала опирайся на локальную базу правил русского языка, "
        "а потом на переданные данные по документам. "
        "Если правило найдено, кратко ссылайся на него и объясняй исправление. "
        "Не придумывай факты, которых нет в сводке.\n\n"
        f"Локальная база правил:\n{rules_context}\n\n"
        f"Состояние:\n{json.dumps({'summary_text': state.get('summary_text'), 'status': state.get('status'), 'documents': compact_documents}, ensure_ascii=False, indent=2)}\n\n"
        f"Вопрос пользователя:\n{message}"
    )

    request_kwargs = {
        "model": DOCUMENT_REVIEW_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }
    if not _resolve_openai_base_url():
        request_kwargs["response_format"] = {"type": "text"}

    try:
        response = client.chat.completions.create(**request_kwargs)
        reply = _safe_text(response.choices[0].message.content)
        if not reply:
            reply = _fallback_chat_answer(message, state)
    except Exception:
        reply = _fallback_chat_answer(message, state)

    return {"reply": reply, "state": state}
