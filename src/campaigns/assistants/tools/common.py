from __future__ import annotations

from typing import Any

from src.campaigns import template_service
from src.campaigns.assistants.client import truncate_text
from src.campaigns.assistants.context import AssistantContext


def tool_def(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def get_snapshot(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "editor_kind": ctx.editor_kind,
        "resource_id": ctx.resource_id,
        "snapshot": ctx.working or ctx.snapshot,
    }


def list_merge_variables(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    variables = ctx.working.get("variables") or ctx.snapshot.get("variables") or []
    if not variables:
        template = template_service.get_template(ctx.resource_id, ctx.owner_username)
        if template and template.get("version"):
            variables = template["version"].get("variables") or []
    return {"ok": True, "variables": variables}


def set_personalization(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(args.get("enabled"))
    saved = template_service.save_version(
        ctx.resource_id,
        ctx.owner_username,
        is_template=enabled,
    )
    if saved is None:
        return {"ok": False, "error": "Шаблон не найден или нет доступа"}
    ctx.working["is_template"] = enabled
    return ctx.emit("set_personalization", enabled=enabled)


def set_subject(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    subject = str(args.get("subject") or "")
    ctx.working["subject"] = subject
    return ctx.emit("set_subject", subject=subject)


def set_body_html(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    html = str(args.get("html") or args.get("body_html") or "")
    ctx.working["body_html"] = truncate_text(html, limit=200000)
    return ctx.emit("set_html", html=html)


def insert_html(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    html = str(args.get("html") or "")
    if not html.strip():
        return {"ok": False, "error": "html пуст"}
    return ctx.emit("insert_html", html=html)


def clip_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(snapshot or {})
    if "body_html" in raw:
        raw["body_html"] = truncate_text(raw.get("body_html"), limit=12000)
    if "html" in raw:
        raw["html"] = truncate_text(raw.get("html"), limit=12000)
    project = raw.get("grapesjs_project")
    if isinstance(project, dict):
        # Keep structure hints but avoid huge payloads in the system prompt.
        raw["grapesjs_project"] = {
            "pages": len(project.get("pages") or []) if isinstance(project.get("pages"), list) else 0,
            "has_assets": bool(project.get("assets")),
            "note": "полный project доступен клиенту; для замены используй set_grapes_project / set_body_html",
        }
    chain = raw.get("chain")
    if isinstance(chain, dict):
        nodes = chain.get("nodes") or []
        edges = chain.get("edges") or []
        raw["chain"] = {
            "version": chain.get("version"),
            "root_node_id": chain.get("root_node_id"),
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
            "edge_count": len(edges) if isinstance(edges, list) else 0,
            "nodes": nodes[:40] if isinstance(nodes, list) else [],
            "edges": edges[:60] if isinstance(edges, list) else [],
        }
    return raw
