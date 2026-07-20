from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.campaigns import docx_text_edits, template_ai, template_service
from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import common

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]

_TEXT_LIMIT = 12000


def _load_docx_bytes(ctx: AssistantContext) -> tuple[dict[str, Any], dict[str, Any], bytes] | dict[str, Any]:
    template = template_service.get_template(ctx.resource_id, ctx.owner_username)
    if not template:
        return {"ok": False, "error": "Шаблон не найден"}
    file_item = template_service.get_template_file(ctx.resource_id, ctx.owner_username)
    if not file_item or not file_item.get("content"):
        return {"ok": False, "error": "Файл документа не найден"}
    filename = str(file_item.get("filename") or (template.get("version") or {}).get("filename") or "document.docx")
    if not filename.lower().endswith(".docx"):
        return {"ok": False, "error": "Точечные правки доступны только для DOCX"}
    return template, file_item, file_item["content"]


def get_docx_meta(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    template = template_service.get_template(ctx.resource_id, ctx.owner_username)
    if not template:
        return {"ok": False, "error": "Шаблон не найден"}
    version = template.get("version") or {}
    return {
        "ok": True,
        "name": template.get("name"),
        "template_type": template.get("template_type"),
        "is_template": template.get("is_template"),
        "filename": version.get("filename"),
        "version_number": version.get("version_number"),
        "variables": version.get("variables") or [],
    }


def get_document_text(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_docx_bytes(ctx)
    if isinstance(loaded, dict):
        return loaded
    _template, file_item, data = loaded
    text = docx_text_edits.extract_plain_text(data, limit=_TEXT_LIMIT)
    return {
        "ok": True,
        "filename": file_item.get("filename"),
        "text": text,
        "truncated": text.endswith("…"),
    }


def replace_text(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    edits_raw = args.get("edits")
    if not isinstance(edits_raw, list) or not edits_raw:
        return {"ok": False, "error": "Нужен непустой список edits: [{find, replace, replace_all?}]"}

    loaded = _load_docx_bytes(ctx)
    if isinstance(loaded, dict):
        return loaded
    template, file_item, data = loaded

    edits: list[dict[str, Any]] = []
    for item in edits_raw:
        if not isinstance(item, dict):
            continue
        find = str(item.get("find") or "")
        if not find:
            continue
        edits.append(
            {
                "find": find,
                "replace": "" if item.get("replace") is None else str(item.get("replace")),
                "replace_all": bool(item.get("replace_all")),
            }
        )
    if not edits:
        return {"ok": False, "error": "В edits нет валидных find/replace"}

    new_bytes, report = docx_text_edits.apply_text_replacements(data, edits)
    applied = sum(1 for item in report if item.get("status") == "applied")
    if applied == 0:
        return {
            "ok": False,
            "error": "Ни одна правка не применена. Проверьте точный фрагмент через get_document_text.",
            "report": report,
        }

    filename = str(file_item.get("filename") or "document.docx")
    updated = template_service.upload_file_version(
        ctx.owner_username,
        name=str(template.get("name") or Path(filename).stem or "Документ"),
        template_type=str(template.get("template_type") or "document"),
        filename=filename,
        data=new_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        template_id=ctx.resource_id,
    )
    ctx.working["name"] = updated.get("name")
    ctx.working["filename"] = (updated.get("version") or {}).get("filename")
    return ctx.emit(
        "reload_template",
        reason="docx_patched",
        template_id=ctx.resource_id,
        version_id=(updated.get("version") or {}).get("id"),
        report=report,
        applied=applied,
    )


def rewrite_document(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    instruction = str(args.get("instruction") or args.get("prompt") or "").strip()
    if not instruction:
        return {"ok": False, "error": "Нужна инструкция для полной пересборки документа"}

    template = template_service.get_template(ctx.resource_id, ctx.owner_username)
    if not template:
        return {"ok": False, "error": "Шаблон не найден"}
    version = template.get("version") or {}
    filename = str(version.get("filename") or "document.docx")
    existing_text = ""
    try:
        file_item = template_service.get_template_file(ctx.resource_id, ctx.owner_username)
        if file_item and file_item.get("content"):
            existing_text = template_service._file_text(filename, file_item["content"])  # noqa: SLF001
    except Exception:
        existing_text = ""

    model = ctx.model or ""
    payload = template_ai._call_llm(  # noqa: SLF001
        model,
        system=(
            "Ты редактор деловых DOCX-документов на русском. "
            "Верни только JSON: "
            '{"name":"...","title":"...","paragraphs":["..."]} '
            "Сохраняй плейсхолдеры {{variable}} если они уместны. "
            "paragraphs — полный новый текст документа абзацами."
        ),
        user=(
            f"Инструкция:\n{instruction}\n\n"
            f"Текущее имя шаблона: {template.get('name')}\n"
            f"Текущий текст документа:\n{existing_text[:_TEXT_LIMIT] or '(пусто)'}"
        ),
    )
    paragraphs = payload.get("paragraphs") or []
    if not isinstance(paragraphs, list):
        paragraphs = [str(paragraphs)]
    doc_bytes = template_ai._docx_from_paragraphs(  # noqa: SLF001
        str(payload.get("title") or payload.get("name") or template.get("name") or "Документ"),
        [str(item) for item in paragraphs],
    )
    safe_name = str(payload.get("name") or template.get("name") or "Документ")
    updated = template_service.upload_file_version(
        ctx.owner_username,
        name=safe_name,
        template_type=str(template.get("template_type") or "document"),
        filename=f"{Path(safe_name).stem or 'document'}.docx",
        data=doc_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        template_id=ctx.resource_id,
    )
    ctx.working["name"] = updated.get("name")
    ctx.working["filename"] = (updated.get("version") or {}).get("filename")
    return ctx.emit(
        "reload_template",
        reason="docx_replaced",
        template_id=ctx.resource_id,
        version_id=(updated.get("version") or {}).get("id"),
    )


TOOLS: list[dict[str, Any]] = [
    common.tool_def(
        "get_docx_meta",
        "Метаданные открытого DOCX-шаблона.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "get_document_text",
        "Прочитать текущий текст DOCX с маркерами абзацев [pN]. Вызывай перед точечными правками.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_merge_variables",
        "Список переменных документа.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "replace_text",
        "Точечная правка DOCX: точная замена фрагментов find→replace с сохранением оформления. "
        "Основной способ изменить текст. find должен совпадать с исходником дословно.",
        {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "Список замен",
                    "items": {
                        "type": "object",
                        "properties": {
                            "find": {
                                "type": "string",
                                "description": "Минимальный точный фрагмент из документа",
                            },
                            "replace": {
                                "type": "string",
                                "description": "На что заменить (готовая замена, не инструкция)",
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "Заменить все вхождения (по умолчанию false — только первое)",
                            },
                        },
                        "required": ["find", "replace"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["edits"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "rewrite_document",
        "Полностью пересобрать DOCX с нуля (потеряет сложное оформление). "
        "Используй ТОЛЬКО если пользователь явно просит переписать документ целиком / создать заново.",
        {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Что должно получиться после полной пересборки",
                }
            },
            "required": ["instruction"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "set_personalization",
        "Включить/выключить персонализацию.",
        {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
    ),
]

HANDLERS: dict[str, ToolHandler] = {
    "get_docx_meta": get_docx_meta,
    "get_document_text": get_document_text,
    "list_merge_variables": common.list_merge_variables,
    "replace_text": replace_text,
    "rewrite_document": rewrite_document,
    "set_personalization": common.set_personalization,
}
