from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List

from docx import Document

try:
    from src.generator.config_generator import DOCUMENT_REVIEW_MODEL, ENABLE_DOCUMENT_REVIEW_AI
    from src.generator.ai_case_agent import (
        _extract_json_payload,
        _resolve_openai_api_key,
        _resolve_openai_base_url,
    )
    from src.generator.philology_knowledge import find_relevant_rules, format_rules_context
except ImportError:  # pragma: no cover
    from generator.config_generator import DOCUMENT_REVIEW_MODEL, ENABLE_DOCUMENT_REVIEW_AI
    from generator.ai_case_agent import (
        _extract_json_payload,
        _resolve_openai_api_key,
        _resolve_openai_base_url,
    )
    from generator.philology_knowledge import find_relevant_rules, format_rules_context

try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None

PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:ADM_NAME|MUN_NAME(?:_[12])?|MUN_R_NAME(?:_1)?|SUB_RF(?:_1)?|HEAD_FIO(?:_1)?|POPULATION|ADRES|EMAIL_OSN|TEL_OSN|REQUISITES_[A-Z]+)\b"
)
POPULATION_PATTERN = re.compile(
    r"(Численность населения проектируемой территории составляет )(\d+)\s+(человек(?:а)?)",
    re.IGNORECASE,
)


@dataclass
class ReviewIssue:
    source: str
    location: str
    fragment: str
    issue: str
    suggestion: str
    severity: str = "warning"


def _safe_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


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


def _population_with_unit(number_text: str) -> str:
    number = int(number_text)
    mod100 = number % 100
    mod10 = number % 10
    if 11 <= mod100 <= 14:
        unit = "человек"
    elif mod10 == 1:
        unit = "человек"
    elif mod10 in (2, 3, 4):
        unit = "человека"
    else:
        unit = "человек"
    return f"{number_text} {unit}"


def _iter_docx_blocks(doc: Document) -> Iterable[tuple[str, str]]:
    for index, paragraph in enumerate(doc.paragraphs, 1):
        text = _safe_text(paragraph.text)
        if text:
            yield (f"paragraph:{index}", text)

    for table_index, table in enumerate(doc.tables, 1):
        for row_index, row in enumerate(table.rows, 1):
            row_text = " | ".join(_safe_text(cell.text.replace("\n", " / ")) for cell in row.cells if _safe_text(cell.text))
            row_text = _safe_text(row_text)
            if row_text:
                yield (f"table:{table_index}:row:{row_index}", row_text)


def _run_local_checks(blocks: Iterable[tuple[str, str]]) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for location, text in blocks:
        placeholder_match = PLACEHOLDER_PATTERN.search(text)
        if placeholder_match:
            placeholder = placeholder_match.group(0)
            issues.append(
                ReviewIssue(
                    source="local",
                    location=location,
                    fragment=text,
                    issue=f"В документе остался незаменённый плейсхолдер `{placeholder}`.",
                    suggestion="Проверить подстановку данных и шаблон.",
                    severity="error",
                )
            )

        for match in POPULATION_PATTERN.finditer(text):
            prefix, number_text, unit = match.groups()
            expected = _population_with_unit(number_text)
            current = f"{number_text} {unit}"
            if current != expected:
                issues.append(
                    ReviewIssue(
                        source="local",
                        location=location,
                        fragment=text,
                        issue=f"После числа используется неверная форма слова: `{current}`.",
                        suggestion=f"{prefix}{expected}.",
                        severity="warning",
                    )
                )

        if "  " in text:
            issues.append(
                ReviewIssue(
                    source="local",
                    location=location,
                    fragment=text,
                    issue="Обнаружены двойные пробелы.",
                    suggestion="Убрать лишние пробелы.",
                    severity="info",
                )
            )
    return issues


def _build_ai_prompt(blocks: list[tuple[str, str]]) -> str:
    payload = [
        {"location": location, "text": text}
        for location, text in blocks
    ]
    rules = find_relevant_rules(" ".join(text for _, text in blocks), limit=5)
    rules_context = format_rules_context(rules)
    return (
        "Проверь текст фрагментов договора/КП на грамматику, падежи, согласование и канцелярский стиль. "
        "Сначала опирайся на локальную базу правил русского языка, затем на сам текст фрагмента. "
        "Не придумывай новый текст целиком. Ищи только реальные ошибки русского языка или шаблонные огрехи. "
        "Если нашлась ошибка, в поле suggestion верни ПОЛНЫЙ ИСПРАВЛЕННЫЙ ФРАГМЕНТ, который можно сразу вставить вместо fragment. "
        "suggestion должен быть готовым текстом документа, а не комментарием редактора. "
        "Нельзя писать 'Заменить ...', 'Исправить ...', 'Нужно ...', 'Следует ...', 'Проверить ...'. "
        "Если для фрагмента нельзя дать безопасную готовую замену, оставь suggestion пустым, но опиши ошибку в issue. "
        "Не меняй смысл документа и не переписывай абзац шире, чем нужно для исправления ошибки. "
        "Верни только JSON-объект вида "
        '{"issues":[{"location":"...","fragment":"...","issue":"...","suggestion":"...","severity":"warning|error|info"}]}. '
        "Если ошибок нет, верни {\"issues\": []}. Без markdown и пояснений вне JSON.\n\n"
        f"Локальная база правил:\n{rules_context}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _run_ai_review(blocks: list[tuple[str, str]], *, ai_enabled: bool = True) -> list[ReviewIssue]:
    if not ai_enabled or not ENABLE_DOCUMENT_REVIEW_AI or not OpenAI:
        return []

    api_key = _resolve_openai_api_key()
    if not api_key:
        return []

    client_kwargs = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url
    if httpx:
        client_kwargs["http_client"] = httpx.Client(
            http2=False,
            timeout=httpx.Timeout(connect=10, read=60, write=60, pool=60),
            trust_env=False,
        )
    client = OpenAI(**client_kwargs)

    request_kwargs = {
        "model": DOCUMENT_REVIEW_MODEL,
        "messages": [{"role": "user", "content": _build_ai_prompt(blocks)}],
    }
    if not base_url:
        request_kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**request_kwargs)
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(_extract_json_payload(content))
    issues = parsed.get("issues") if isinstance(parsed, dict) else []
    result: list[ReviewIssue] = []
    for item in issues or []:
        if not isinstance(item, dict):
            continue
        suggestion = _safe_text(item.get("suggestion"))
        if _looks_like_editorial_instruction(suggestion):
            suggestion = ""
        result.append(
            ReviewIssue(
                source="ai",
                location=_safe_text(item.get("location")),
                fragment=_safe_text(item.get("fragment")),
                issue=_safe_text(item.get("issue")),
                suggestion=suggestion,
                severity=_safe_text(item.get("severity")) or "warning",
            )
        )
    return result


def review_docx(document_path: Path, *, ai_enabled: bool = True) -> dict:
    doc = Document(document_path)
    blocks = list(_iter_docx_blocks(doc))
    local_issues = _run_local_checks(blocks)
    ai_issues: list[ReviewIssue] = []
    ai_error = None
    try:
        ai_issues = _run_ai_review(blocks, ai_enabled=ai_enabled)
    except Exception as exc:  # pragma: no cover
        ai_error = f"{type(exc).__name__}: {exc}"

    issues = local_issues + ai_issues
    return {
        "document": str(document_path),
        "issue_count": len(issues),
        "local_issue_count": len(local_issues),
        "ai_issue_count": len(ai_issues),
        "ai_enabled": bool(ai_enabled and ENABLE_DOCUMENT_REVIEW_AI),
        "ai_model": DOCUMENT_REVIEW_MODEL,
        "ai_error": ai_error,
        "issues": [asdict(issue) for issue in issues],
    }


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python document_review_agent.py <path-to-docx> [json-output-path]")
        return 1

    document_path = Path(argv[1])
    result = review_docx(document_path)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)

    if len(argv) >= 3:
        output_path = Path(argv[2])
        output_path.write_text(payload, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
