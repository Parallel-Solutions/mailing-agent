"""Email chain graph storage, validation, and branch token management."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select

from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignChainToken, CampaignRecipient, EmailChainRecord
from src.security.company_access import apply_owner_filter, can_access_owner

CHAIN_VERSION = 2

NODE_KIND_EMAIL = "email"
NODE_KIND_LINK = "link"
LINK_KIND_CUSTOM = "custom"
LINK_KIND_UNSUBSCRIBE = "unsubscribe"
LINK_KIND_SUBSCRIBE = "subscribe"
VALID_NODE_KINDS = {NODE_KIND_EMAIL, NODE_KIND_LINK}
VALID_LINK_KINDS = {LINK_KIND_CUSTOM, LINK_KIND_UNSUBSCRIBE, LINK_KIND_SUBSCRIBE}

_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str = "") -> str:
    value = uuid.uuid4().hex[:12]
    return f"{prefix}{value}" if prefix else value


def _normalize_node_kind(raw: Any) -> str:
    kind = str(raw or NODE_KIND_EMAIL).strip().lower()
    return kind if kind in VALID_NODE_KINDS else NODE_KIND_EMAIL


def _normalize_link_kind(raw: Any) -> str | None:
    if raw is None:
        return None
    kind = str(raw).strip().lower()
    return kind if kind in VALID_LINK_KINDS else None


def _is_valid_http_url(url: str) -> bool:
    value = str(url or "").strip()
    if not value or not _URL_PATTERN.match(value):
        return False
    parsed = urlparse(value)
    return bool(parsed.netloc)


def node_kind(node: dict[str, Any]) -> str:
    return _normalize_node_kind(node.get("kind"))


def is_link_node(node: dict[str, Any]) -> bool:
    return node_kind(node) == NODE_KIND_LINK


def is_email_node(node: dict[str, Any]) -> bool:
    return node_kind(node) == NODE_KIND_EMAIL


def empty_chain() -> dict[str, Any]:
    root_id = _new_id("node-")
    return {
        "version": CHAIN_VERSION,
        "root_node_id": root_id,
        "nodes": [
            {
                "id": root_id,
                "name": "Письмо 1",
                "kind": NODE_KIND_EMAIL,
                "email_template_id": None,
                "document_template_ids": [],
            }
        ],
        "edges": [],
    }


def get_email_chain(campaign: Campaign, *, session=None) -> dict[str, Any]:
    if campaign.email_chain_id:
        row = _get_chain_row(campaign.email_chain_id, session=session)
        if row is not None:
            payload = row.payload if isinstance(row.payload, dict) else {}
            if payload.get("nodes"):
                return normalize_chain(payload)
    draft = dict(campaign.draft_payload or {})
    chain = draft.get("email_chain")
    if isinstance(chain, dict) and chain.get("nodes"):
        return normalize_chain(chain)
    return empty_chain()


def _get_chain_row(chain_id: str, *, session=None) -> EmailChainRecord | None:
    if session is not None:
        return session.get(EmailChainRecord, chain_id)
    with session_scope() as scoped:
        return scoped.get(EmailChainRecord, chain_id)


def _chain_state(row: EmailChainRecord) -> dict[str, Any]:
    chain = normalize_chain(row.payload if isinstance(row.payload, dict) else empty_chain())
    validation = validate_chain(chain, strict=False)
    return {
        "id": row.id,
        "name": row.name,
        "chain": chain,
        "validation": validation,
        "published": bool(row.published),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _ensure_chain_access(
    row: EmailChainRecord | None,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> EmailChainRecord:
    if row is None or not can_access_owner(visible_owners, row.owner_username):
        raise ValueError("Цепочка не найдена")
    return row


def create_chain(owner_username: str, *, name: str | None = None) -> dict[str, Any]:
    chain = empty_chain()
    with session_scope() as session:
        row = EmailChainRecord(
            id=_new_id(),
            owner_username=owner_username,
            name=str(name or "Новая цепочка"),
            payload=chain,
            published=False,
        )
        session.add(row)
        session.flush()
        return _chain_state(row)


def list_chains(
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    from sqlalchemy import select

    with session_scope() as session:
        stmt = select(EmailChainRecord).order_by(EmailChainRecord.updated_at.desc())
        stmt = apply_owner_filter(stmt, EmailChainRecord.owner_username, visible_owners)
        rows = session.scalars(stmt.limit(limit).offset(offset)).all()
        total_stmt = select(func.count()).select_from(EmailChainRecord)
        total_stmt = apply_owner_filter(total_stmt, EmailChainRecord.owner_username, visible_owners)
        total = int(session.scalar(total_stmt) or 0)
        return {
            "items": [
                {
                    "id": row.id,
                    "name": row.name,
                    "published": bool(row.published),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in rows
            ],
            "total": total,
        }


def load_chain(chain_id: str, owner_username: str, *, visible_owners: frozenset[str] | None = None) -> dict[str, Any]:
    with session_scope() as session:
        row = _ensure_chain_access(session.get(EmailChainRecord, chain_id), owner_username, visible_owners=visible_owners)
        return _chain_state(row)


def save_chain(chain_id: str, owner_username: str, chain: dict[str, Any], *, visible_owners: frozenset[str] | None = None) -> dict[str, Any]:
    normalized = normalize_chain(chain)
    with session_scope() as session:
        row = _ensure_chain_access(session.get(EmailChainRecord, chain_id), owner_username, visible_owners=visible_owners)
        row.payload = normalized
        row.updated_at = _now()
        session.flush()
        return _chain_state(row)


def update_chain(
    chain_id: str,
    owner_username: str,
    *,
    name: str | None = None,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    if name is None:
        raise ValueError("Не указано название цепочки")
    trimmed = str(name).strip()
    if not trimmed:
        raise ValueError("Укажите название цепочки")
    with session_scope() as session:
        row = _ensure_chain_access(session.get(EmailChainRecord, chain_id), owner_username, visible_owners=visible_owners)
        row.name = trimmed[:255]
        row.updated_at = _now()
        session.flush()
        return _chain_state(row)


def publish_chain(chain_id: str, owner_username: str, *, visible_owners: frozenset[str] | None = None) -> dict[str, Any]:
    with session_scope() as session:
        row = _ensure_chain_access(session.get(EmailChainRecord, chain_id), owner_username, visible_owners=visible_owners)
        validation = validate_chain(row.payload if isinstance(row.payload, dict) else empty_chain(), strict=True)
        if not validation["ok"]:
            raise ValueError("; ".join(validation["errors"]))
        normalized = validation["chain"]
        row.payload = normalized
        row.published = True
        row.updated_at = _now()
        session.flush()
        return _chain_state(row)


def resolve_button_label(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> str:
    """Button text in the parent email matches the target block name."""
    target_id = str(edge.get("target_id") or "")
    target = node_by_id.get(target_id) or {}
    target_name = str(target.get("name") or "").strip()
    if target_name:
        return target_name
    return str(edge.get("button_label") or "Перейти").strip() or "Перейти"


def _normalize_node(raw: dict[str, Any]) -> dict[str, Any]:
    node_id = str(raw.get("id") or _new_id("node-"))
    kind = _normalize_node_kind(raw.get("kind"))
    doc_ids = raw.get("document_template_ids") or []
    if not isinstance(doc_ids, list):
        doc_ids = []
    node: dict[str, Any] = {
        "id": node_id,
        "name": str(raw.get("name") or "Письмо"),
        "kind": kind,
    }
    if kind == NODE_KIND_LINK:
        link_kind = _normalize_link_kind(raw.get("link_kind")) or LINK_KIND_CUSTOM
        node["link_kind"] = link_kind
        node["link_url"] = str(raw.get("link_url") or "").strip() or None
        if link_kind != LINK_KIND_CUSTOM:
            node["link_url"] = None
    else:
        node["email_template_id"] = raw.get("email_template_id") or None
        node["document_template_ids"] = [str(x) for x in doc_ids if x]
    return node


def normalize_chain(chain: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for raw in chain.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        nodes.append(_normalize_node(raw))
    node_ids = {n["id"] for n in nodes}
    node_by_id = {n["id"]: n for n in nodes}
    edges = []
    for raw in chain.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_id") or "")
        target_id = str(raw.get("target_id") or "")
        if source_id not in node_ids or target_id not in node_ids:
            continue
        edge = {
            "id": str(raw.get("id") or _new_id("edge-")),
            "source_id": source_id,
            "target_id": target_id,
            "button_label": str(raw.get("button_label") or "Перейти"),
        }
        edge["button_label"] = resolve_button_label(edge, node_by_id)
        edges.append(edge)
    root_node_id = str(chain.get("root_node_id") or "")
    if root_node_id not in node_ids and nodes:
        root_node_id = nodes[0]["id"]
    if not nodes:
        return empty_chain()
    return {
        "version": CHAIN_VERSION,
        "root_node_id": root_node_id,
        "nodes": nodes,
        "edges": edges,
    }


def validate_chain(chain: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    normalized = normalize_chain(chain)
    nodes = normalized["nodes"]
    edges = normalized["edges"]
    root_id = normalized["root_node_id"]
    node_by_id = {n["id"]: n for n in nodes}
    incoming: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    outgoing: dict[str, list[str]] = {n["id"]: [] for n in nodes}

    for edge in edges:
        incoming[edge["target_id"]].append(edge["id"])
        outgoing[edge["source_id"]].append(edge["id"])
        if strict:
            target = node_by_id.get(edge["target_id"]) or {}
            target_name = str(target.get("name") or "").strip()
            if not target_name:
                errors.append(
                    f"У блока «{target.get('name') or edge['target_id']}» не указано название (для кнопки)"
                )

    roots = [nid for nid, inc in incoming.items() if not inc]
    if len(roots) != 1:
        errors.append("Цепочка должна иметь ровно один начальный блок")
    elif roots[0] != root_id:
        errors.append("root_node_id не совпадает с начальным блоком")

    root_node = node_by_id.get(root_id) or {}
    if root_id and not is_email_node(root_node):
        errors.append("Начальный блок должен быть письмом")

    for node in nodes:
        name = node.get("name") or node["id"]
        if is_email_node(node):
            if not str(node.get("email_template_id") or "").strip():
                message = f"У блока «{name}» не выбран шаблон письма"
                if strict:
                    errors.append(message)
                else:
                    warnings.append(message)
        elif is_link_node(node):
            link_kind = _normalize_link_kind(node.get("link_kind"))
            if link_kind not in VALID_LINK_KINDS:
                errors.append(f"У блока «{name}» не указан тип ссылки")
            elif link_kind == LINK_KIND_CUSTOM:
                url = str(node.get("link_url") or "").strip()
                if not url:
                    errors.append(f"У блока «{name}» не указан URL")
                elif not _is_valid_http_url(url):
                    errors.append(f"У блока «{name}» указан некорректный URL")
            if outgoing.get(node["id"]):
                errors.append(f"Блок-ссылка «{name}» не может иметь дочерние блоки")

    # Reachability and cycle detection from root
    if root_id in node_by_id:
        visited: set[str] = set()
        stack = [root_id]
        while stack:
            current = stack.pop()
            if current in visited:
                errors.append("Обнаружен цикл в цепочке")
                break
            visited.add(current)
            for edge in edges:
                if edge["source_id"] == current:
                    stack.append(edge["target_id"])
        unreachable = [n["name"] for n in nodes if n["id"] not in visited]
        if unreachable:
            errors.append(f"Недостижимые блоки: {', '.join(unreachable)}")

    for node in nodes:
        if node["id"] != root_id and not incoming.get(node["id"]):
            errors.append(f"Блок «{node.get('name')}» не связан с цепочкой")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "chain": normalized,
    }


def save_email_chain(campaign_id: str, owner_username: str, chain: dict[str, Any], *, visible_owners: frozenset[str] | None = None) -> dict[str, Any]:
    normalized = normalize_chain(chain)
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise ValueError("Рассылка не найдена")
        if camp.email_chain_id:
            row = session.get(EmailChainRecord, camp.email_chain_id)
            if row is not None:
                row.payload = normalized
                row.updated_at = _now()
        else:
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = normalized
            camp.draft_payload = draft
        camp.updated_at = _now()
        session.flush()
        validation = validate_chain(normalized, strict=False)
        published = camp.send_scenario == "email_chain"
        if camp.email_chain_id:
            row = session.get(EmailChainRecord, camp.email_chain_id)
            if row is not None:
                published = bool(row.published)
        return {
            "chain": normalized,
            "validation": validation,
            "published": published,
        }


def load_email_chain(campaign_id: str, owner_username: str, *, visible_owners: frozenset[str] | None = None) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise ValueError("Рассылка не найдена")
        chain = get_email_chain(camp, session=session)
        validation = validate_chain(chain, strict=False)
        published = camp.send_scenario == "email_chain"
        if camp.email_chain_id:
            row = session.get(EmailChainRecord, camp.email_chain_id)
            if row is not None:
                published = bool(row.published)
        return {
            "chain": chain,
            "validation": validation,
            "published": published,
        }


def publish_email_chain(campaign_id: str, owner_username: str, *, visible_owners: frozenset[str] | None = None) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise ValueError("Рассылка не найдена")
        chain = get_email_chain(camp, session=session)
        validation = validate_chain(chain, strict=True)
        if not validation["ok"]:
            raise ValueError("; ".join(validation["errors"]))
        normalized = validation["chain"]
        if camp.email_chain_id:
            row = session.get(EmailChainRecord, camp.email_chain_id)
            if row is not None:
                row.payload = normalized
                row.published = True
                row.updated_at = _now()
        else:
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = normalized
            camp.draft_payload = draft
        camp.send_scenario = "email_chain"
        root = next((n for n in normalized["nodes"] if n["id"] == normalized["root_node_id"]), None)
        if root and root.get("email_template_id"):
            camp.email_template_id = root["email_template_id"]
        camp.updated_at = _now()
        session.flush()
        return {
            "chain": normalized,
            "validation": validation,
            "published": True,
            "campaign_id": campaign_id,
        }


def create_branch_tokens(
    *,
    campaign_id: str,
    recipient_id: int,
    source_node_id: str,
    edges: list[dict[str, Any]],
    test_email: str | None = None,
) -> list[CampaignChainToken]:
    tokens: list[CampaignChainToken] = []
    for edge in edges:
        token = CampaignChainToken(
            token=str(uuid.uuid4()),
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            edge_id=str(edge["id"]),
            source_node_id=source_node_id,
            target_node_id=str(edge["target_id"]),
            send_status="pending",
            test_email=test_email,
        )
        tokens.append(token)
    return tokens


def record_branch_click(token: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(CampaignChainToken, token)
        if row is None:
            raise ValueError("Ссылка не найдена")
        recipient = session.get(CampaignRecipient, int(row.recipient_id))
        if recipient is None:
            raise ValueError("Получатель не найден")
        already_clicked = row.clicked_at is not None
        if not already_clicked:
            row.clicked_at = _now()
            if not row.test_email:
                extra = dict(recipient.extra or {})
                chain_state = dict(extra.get("chain") or {})
                chain_state["current_node_id"] = row.target_node_id
                clicked_edges = list(chain_state.get("clicked_edges") or [])
                if row.edge_id not in clicked_edges:
                    clicked_edges.append(row.edge_id)
                chain_state["clicked_edges"] = clicked_edges
                extra["chain"] = chain_state
                recipient.extra = extra
            session.flush()
        return {
            "token": row.token,
            "campaign_id": row.campaign_id,
            "recipient_id": row.recipient_id,
            "edge_id": row.edge_id,
            "source_node_id": row.source_node_id,
            "target_node_id": row.target_node_id,
            "already_clicked": already_clicked,
            "send_status": row.send_status,
            "test_email": row.test_email,
        }


def mark_token_sent(token: str, *, error: str | None = None, status: str | None = None) -> None:
    with session_scope() as session:
        row = session.get(CampaignChainToken, token)
        if row is None:
            return
        if error:
            row.send_status = "error"
            row.error = error
        elif status:
            row.send_status = status
        else:
            row.send_status = "sent"
            row.sent_at = _now()
        session.flush()


def get_chain_click_stats(campaign_id: str) -> dict[str, Any]:
    from src.campaigns.chain_consent_service import get_consent_stats

    with session_scope() as session:
        rows = session.execute(
            select(
                CampaignChainToken.edge_id,
                func.count(CampaignChainToken.token).label("total"),
                func.count(CampaignChainToken.clicked_at).label("clicks"),
            )
            .where(CampaignChainToken.campaign_id == campaign_id)
            .group_by(CampaignChainToken.edge_id)
        ).all()
        return {
            "edges": [
                {"edge_id": r.edge_id, "tokens": int(r.total), "clicks": int(r.clicks or 0)}
                for r in rows
            ],
            "consents": get_consent_stats(campaign_id, session=session),
        }


def find_node(chain: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in chain.get("nodes") or []:
        if node.get("id") == node_id:
            return node
    return None


def outgoing_edges(chain: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return [e for e in chain.get("edges") or [] if e.get("source_id") == node_id]
