from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from docx import Document

from src.generator.ai_case_agent import (
    OpenAI,
    _extract_json_payload,
    _resolve_openai_api_key,
    _resolve_openai_base_url,
)
from src.generator.config_generator import DOCUMENT_REVIEW_MODEL, OUTPUT_DIR
from src.generator.document_review_agent import review_docx
from src.generator.pdf_converter import convert_docx_batch

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
}


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


def _normalize_double_spaces(text: str) -> str:
    while "  " in text:
        text = text.replace("  ", " ")
    return text


def _apply_issue_to_document(location_map: dict[str, Any], issue: dict[str, Any]) -> bool:
    location = _safe_text(issue.get("location"))
    paragraph = location_map.get(location)
    if paragraph is None:
        return False

    issue_text = _safe_text(issue.get("issue")).lower()
    fragment = _safe_text(issue.get("fragment"))
    suggestion = _safe_text(issue.get("suggestion"))
    current_text = paragraph.text

    if "двойные пробелы" in issue_text:
        normalized = _normalize_double_spaces(current_text)
        return _replace_paragraph_text(paragraph, normalized)

    if "после числа используется неверная форма слова" in issue_text and suggestion:
        return _replace_paragraph_text(paragraph, suggestion)

    if issue.get("source") == "ai" and suggestion and fragment and current_text.strip() == fragment.strip():
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


def run_philologist(*, output_dir: Path | None = None, ai_enabled: bool = True) -> dict[str, Any]:
    target_dir = output_dir or OUTPUT_DIR
    docx_paths = sorted(target_dir.rglob("*.docx"))

    state = PHILOLOGIST_STATE
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
            "summary_text": "Агент-филолог начал проверку документов.",
        }
    )

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

    state["status"] = "completed"
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["elapsed_seconds"] = round(perf_counter() - started_at, 2)
    state["summary_text"] = _format_summary(processed_documents)
    return dict(state)


def get_philologist_status() -> dict[str, Any]:
    return dict(PHILOLOGIST_STATE)


def _fallback_chat_answer(message: str, state: dict[str, Any]) -> str:
    lowered = message.lower()
    documents = state.get("documents") or []
    if not documents:
        return "Агент-филолог ещё не запускался. Сначала запусти проверку документов."
    if "что исправ" in lowered:
        fixed = [item for item in documents if item.get("applied_fix_count", 0) > 0]
        return _format_fixed_details(fixed)
    if "ошиб" in lowered or "замеч" in lowered:
        issues = [item for item in documents if item.get("issue_count", 0) > 0]
        return _format_issue_details(issues)
    return state.get("summary_text") or "Проверка завершена, но краткая сводка пока недоступна."


def chat_with_philologist(message: str) -> dict[str, Any]:
    state = get_philologist_status()
    client = _build_llm_client()
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
        "Если пользователь спрашивает об ошибках или исправлениях, опирайся только на переданные данные. "
        "Не придумывай факты, которых нет в сводке.\n\n"
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
