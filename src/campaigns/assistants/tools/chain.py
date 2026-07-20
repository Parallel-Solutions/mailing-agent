from __future__ import annotations

import secrets
from typing import Any, Callable

from src.campaigns import template_service
from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import common

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]


def _chain(ctx: AssistantContext) -> dict[str, Any]:
    chain = ctx.working.get("chain")
    if not isinstance(chain, dict):
        chain = dict(ctx.snapshot.get("chain") or {})
        ctx.working["chain"] = chain
    chain.setdefault("nodes", [])
    chain.setdefault("edges", [])
    return chain


def _emit_chain(ctx: AssistantContext) -> dict[str, Any]:
    chain = _chain(ctx)
    return ctx.emit("chain_set", chain=chain, selected_node_id=ctx.working.get("selected_node_id"))


def get_chain(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "chain": _chain(ctx),
        "selected_node_id": ctx.working.get("selected_node_id") or ctx.snapshot.get("selected_node_id"),
    }


def list_email_templates(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    items = template_service.list_templates(ctx.owner_username, template_type="email")
    return {
        "ok": True,
        "templates": [
            {"id": item["id"], "name": item.get("name"), "subject": (item.get("version") or {}).get("subject")}
            for item in items[:80]
        ],
    }


def list_document_templates(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    items = template_service.list_templates(ctx.owner_username, template_type="document")
    result = []
    for item in items[:80]:
        filename = (item.get("version") or {}).get("filename")
        if not filename:
            continue
        result.append({"id": item["id"], "name": item.get("name"), "filename": filename})
    return {"ok": True, "templates": result}


def add_email_node(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    chain = _chain(ctx)
    parent_id = str(args.get("parent_id") or chain.get("root_node_id") or "")
    if not parent_id:
        return {"ok": False, "error": "parent_id обязателен"}
    child_id = f"node-{secrets.token_hex(4)}"
    child_name = str(args.get("name") or f"Письмо {len(chain['nodes']) + 1}")
    child = {
        "id": child_id,
        "name": child_name,
        "kind": "email",
        "email_template_id": args.get("email_template_id"),
        "document_template_ids": list(args.get("document_template_ids") or []),
    }
    chain["nodes"] = list(chain["nodes"]) + [child]
    chain["edges"] = list(chain["edges"]) + [
        {
            "id": f"edge-{secrets.token_hex(4)}",
            "source_id": parent_id,
            "target_id": child_id,
            "button_label": child_name,
        }
    ]
    ctx.working["selected_node_id"] = child_id
    return _emit_chain(ctx)


def add_link_node(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    chain = _chain(ctx)
    parent_id = str(args.get("parent_id") or "")
    if not parent_id:
        return {"ok": False, "error": "parent_id обязателен"}
    link_kind = str(args.get("link_kind") or "custom")
    if link_kind not in {"custom", "unsubscribe", "subscribe"}:
        return {"ok": False, "error": "link_kind должен быть custom|unsubscribe|subscribe"}
    defaults = {
        "custom": "Ссылка",
        "unsubscribe": "Отписаться",
        "subscribe": "Подписаться",
    }
    child_id = f"node-{secrets.token_hex(4)}"
    child_name = str(args.get("name") or defaults[link_kind])
    child: dict[str, Any] = {
        "id": child_id,
        "name": child_name,
        "kind": "link",
        "link_kind": link_kind,
        "link_url": str(args.get("link_url") or "") if link_kind == "custom" else None,
    }
    chain["nodes"] = list(chain["nodes"]) + [child]
    chain["edges"] = list(chain["edges"]) + [
        {
            "id": f"edge-{secrets.token_hex(4)}",
            "source_id": parent_id,
            "target_id": child_id,
            "button_label": child_name,
        }
    ]
    ctx.working["selected_node_id"] = child_id
    return _emit_chain(ctx)


def remove_node(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    chain = _chain(ctx)
    node_id = str(args.get("node_id") or "")
    if not node_id:
        return {"ok": False, "error": "node_id обязателен"}
    if node_id == chain.get("root_node_id"):
        return {"ok": False, "error": "Корневой узел удалять нельзя"}
    to_remove = {node_id}
    changed = True
    while changed:
        changed = False
        for edge in chain.get("edges") or []:
            if edge.get("source_id") in to_remove and edge.get("target_id") not in to_remove:
                to_remove.add(str(edge["target_id"]))
                changed = True
    chain["nodes"] = [n for n in chain["nodes"] if n.get("id") not in to_remove]
    chain["edges"] = [
        e
        for e in chain["edges"]
        if e.get("source_id") not in to_remove and e.get("target_id") not in to_remove
    ]
    if ctx.working.get("selected_node_id") in to_remove:
        ctx.working["selected_node_id"] = chain.get("root_node_id")
    return _emit_chain(ctx)


def update_node(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    chain = _chain(ctx)
    node_id = str(args.get("node_id") or "")
    if not node_id:
        return {"ok": False, "error": "node_id обязателен"}
    patch = {k: v for k, v in args.items() if k != "node_id" and v is not None}
    nodes = []
    found = False
    for node in chain["nodes"]:
        if node.get("id") != node_id:
            nodes.append(node)
            continue
        found = True
        next_node = dict(node)
        for key in (
            "name",
            "kind",
            "email_template_id",
            "document_template_ids",
            "link_kind",
            "link_url",
        ):
            if key in patch:
                next_node[key] = patch[key]
        nodes.append(next_node)
        if "name" in patch and node_id != chain.get("root_node_id"):
            chain["edges"] = [
                (
                    {**edge, "button_label": str(patch["name"] or "Перейти")}
                    if edge.get("target_id") == node_id
                    else edge
                )
                for edge in chain["edges"]
            ]
    if not found:
        return {"ok": False, "error": "Узел не найден"}
    chain["nodes"] = nodes
    ctx.working["selected_node_id"] = node_id
    return _emit_chain(ctx)


def set_selected_node(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    node_id = str(args.get("node_id") or "")
    if not node_id:
        return {"ok": False, "error": "node_id обязателен"}
    ctx.working["selected_node_id"] = node_id
    return ctx.emit("chain_select_node", node_id=node_id)


TOOLS: list[dict[str, Any]] = [
    common.tool_def(
        "get_chain",
        "Прочитать текущую цепочку и выбранный узел.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_email_templates",
        "Список шаблонов писем для привязки к узлам.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_document_templates",
        "Список документных шаблонов (вложения).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "add_email_node",
        "Добавить дочерний email-узел.",
        {
            "type": "object",
            "properties": {
                "parent_id": {"type": "string"},
                "name": {"type": "string"},
                "email_template_id": {"type": "string"},
                "document_template_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["parent_id"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "add_link_node",
        "Добавить дочерний link-узел.",
        {
            "type": "object",
            "properties": {
                "parent_id": {"type": "string"},
                "link_kind": {"type": "string", "enum": ["custom", "unsubscribe", "subscribe"]},
                "name": {"type": "string"},
                "link_url": {"type": "string"},
            },
            "required": ["parent_id", "link_kind"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "remove_node",
        "Удалить узел и его поддерево (кроме корня).",
        {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "update_node",
        "Обновить поля узла (имя, шаблоны, ссылку).",
        {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["email", "link"]},
                "email_template_id": {"type": "string"},
                "document_template_ids": {"type": "array", "items": {"type": "string"}},
                "link_kind": {"type": "string", "enum": ["custom", "unsubscribe", "subscribe"]},
                "link_url": {"type": "string"},
            },
            "required": ["node_id"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "set_selected_node",
        "Выбрать узел на схеме.",
        {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
            "additionalProperties": False,
        },
    ),
]

HANDLERS: dict[str, ToolHandler] = {
    "get_chain": get_chain,
    "list_email_templates": list_email_templates,
    "list_document_templates": list_document_templates,
    "add_email_node": add_email_node,
    "add_link_node": add_link_node,
    "remove_node": remove_node,
    "update_node": update_node,
    "set_selected_node": set_selected_node,
}
