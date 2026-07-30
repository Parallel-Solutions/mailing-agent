"""Per-link click analytics for standalone emails and email chains."""

from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from src.campaigns.chain_service import get_chain_click_stats
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, MailTemplate, TemplateVersion

_TEXT_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CLICK_STATUSES = {
    "click",
    "clicked",
    "external_mail.click",
    "ok_link_visited",
}


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "a":
            href = attrs_map.get("href", "").strip()
            if href:
                self._current = {
                    "url": href,
                    "label_parts": [
                        attrs_map.get("aria-label", ""),
                        attrs_map.get("title", ""),
                    ],
                }
        elif tag.lower() == "img" and self._current is not None:
            self._current["label_parts"].append(attrs_map.get("alt", ""))

    def handle_data(self, data: str) -> None:
        if self._current is not None and data.strip():
            self._current["label_parts"].append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current is None:
            return
        label = " ".join(
            part.strip()
            for part in self._current.get("label_parts") or []
            if str(part).strip()
        )
        self.links.append(
            {
                "url": str(self._current.get("url") or ""),
                "label": " ".join(label.split()),
            }
        )
        self._current = None


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _empty_link_analytics(*, mode: str = "standalone") -> dict[str, Any]:
    return {
        "mode": mode,
        "has_links": False,
        "has_documents": False,
        "total_clicks": 0,
        "unique_clickers": 0,
        "steps": [],
    }


def _normalize_url(value: Any) -> str:
    raw = html.unescape(_safe_text(value)).rstrip(".,);]}>")
    if not raw.lower().startswith(("http://", "https://")):
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            parsed.query,
            "",
        )
    )


def _url_label(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc}{path}" if path else parsed.netloc


def _template_links(body_html: str, body_text: str) -> list[dict[str, str]]:
    parser = _AnchorParser()
    try:
        parser.feed(body_html or "")
    except Exception:
        parser.links = []

    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        url = _normalize_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(
            {
                "url": url,
                "label": _safe_text(item.get("label")) or _url_label(url),
            }
        )
    for match in _TEXT_URL_RE.finditer(body_text or ""):
        url = _normalize_url(match.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        links.append({"url": url, "label": _url_label(url)})
    return links


def _nested_text(payload: Any, keys: tuple[str, ...], *, depth: int = 0) -> str:
    if depth > 4 or not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return _safe_text(value)
    for key in ("payload", "data", "event", "event_data", "metadata"):
        nested = payload.get(key)
        result = _nested_text(nested, keys, depth=depth + 1)
        if result:
            return result
    return ""


def _provider_click_events(job_id: str) -> list[dict[str, Any]]:
    from src.generator.delivery.mailopost_events import load_mailopost_events
    from src.generator.delivery.rusender_events import load_rusender_events
    from src.generator.delivery.unisender_go_events import load_unisender_go_events

    sources = (
        ("rusender", load_rusender_events),
        ("mailopost", load_mailopost_events),
        ("unisender_go", load_unisender_go_events),
    )
    click_events: list[dict[str, Any]] = []
    for provider, loader in sources:
        for record in loader(job_id):
            status = _safe_text(
                record.get("provider_status") or record.get("event_type")
            ).lower()
            if status not in _CLICK_STATUSES and "click" not in status and "link_visited" not in status:
                continue
            url = _normalize_url(_nested_text(record, ("url", "link", "href")))
            if not url:
                continue
            click_events.append(
                {
                    "url": url,
                    "email": _safe_text(
                        record.get("recipient")
                        or _nested_text(record, ("email", "recipient", "to"))
                    ).lower(),
                    "row_id": _safe_text(
                        record.get("row_id")
                        or _nested_text(record, ("app_row_id", "row_id"))
                    ),
                    "clicked_at": _safe_text(
                        record.get("occurred_at")
                        or record.get("received_at")
                        or _nested_text(record, ("event_time", "created_at", "timestamp"))
                    ),
                    "provider": provider,
                }
            )
    return click_events


def _standalone_link_analytics(job_id: str, campaign: dict[str, Any]) -> dict[str, Any]:
    campaign_id = _safe_text(campaign.get("id"))
    template_id = _safe_text(campaign.get("email_template_id"))
    body_html = ""
    body_text = ""
    step_name = "Письмо"
    recipient_by_email: dict[str, dict[str, Any]] = {}

    with session_scope() as session:
        campaign_row = session.get(Campaign, campaign_id) if campaign_id else None
        if campaign_row is not None:
            draft = campaign_row.draft_payload if isinstance(campaign_row.draft_payload, dict) else {}
            body_html = _safe_text(draft.get("email_body") or draft.get("body_html"))
            body_text = _safe_text(draft.get("email_body_text") or draft.get("body_text"))
            template_id = template_id or _safe_text(campaign_row.email_template_id)
            recipient_by_email = {
                _safe_text(recipient.email).lower(): {
                    "id": int(recipient.id),
                    "row_index": int(recipient.row_index),
                    "company": _safe_text(recipient.company),
                    "contact_name": _safe_text(recipient.contact_name),
                }
                for recipient in session.scalars(
                    select(CampaignRecipient).where(
                        CampaignRecipient.campaign_id == campaign_row.id
                    )
                ).all()
                if _safe_text(recipient.email)
            }
        if template_id:
            template = session.get(MailTemplate, template_id)
            if template is not None:
                step_name = _safe_text(template.name) or step_name
                version = (
                    session.get(TemplateVersion, template.active_version_id)
                    if template.active_version_id
                    else None
                )
                if version is not None:
                    body_html = _safe_text(version.body_html) or body_html
                    body_text = _safe_text(version.body_text) or body_text

    declared_links = _template_links(body_html, body_text)
    click_events = _provider_click_events(job_id)
    links_by_url: dict[str, dict[str, Any]] = {}
    for item in declared_links:
        url = item["url"]
        links_by_url[url] = {
            "id": "link-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
            "label": item["label"],
            "kind": "custom",
            "url": url,
            "clicks": 0,
            "unique_clickers": 0,
            "clickers": [],
        }

    clicker_keys_by_url: dict[str, set[str]] = {}
    for event in click_events:
        url = event["url"]
        link = links_by_url.setdefault(
            url,
            {
                "id": "link-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                "label": _url_label(url),
                "kind": "custom",
                "url": url,
                "clicks": 0,
                "unique_clickers": 0,
                "clickers": [],
            },
        )
        link["clicks"] += 1
        email = _safe_text(event.get("email")).lower()
        clicker_key = email or _safe_text(event.get("row_id")) or f"event-{link['clicks']}"
        seen = clicker_keys_by_url.setdefault(url, set())
        if clicker_key in seen:
            continue
        seen.add(clicker_key)
        recipient = recipient_by_email.get(email)
        link["clickers"].append(
            {
                "recipient_id": int(recipient["id"]) if recipient is not None else None,
                "row_id": (
                    str(recipient["row_index"])
                    if recipient is not None
                    else _safe_text(event.get("row_id"))
                ),
                "email": email,
                "company": _safe_text(recipient["company"]) if recipient is not None else "",
                "contact_name": (
                    _safe_text(recipient["contact_name"]) if recipient is not None else ""
                ),
                "clicked_at": _safe_text(event.get("clicked_at")),
                "provider": _safe_text(event.get("provider")),
            }
        )
        link["unique_clickers"] = len(link["clickers"])

    links = list(links_by_url.values())
    if not links:
        return _empty_link_analytics()

    unique_clickers = {
        _safe_text(clicker.get("email")).lower()
        or _safe_text(clicker.get("recipient_id"))
        or _safe_text(clicker.get("row_id"))
        for link in links
        for clicker in link.get("clickers") or []
    }
    unique_clickers.discard("")
    return {
        "mode": "standalone",
        "has_links": True,
        "has_documents": False,
        "total_clicks": sum(int(link.get("clicks") or 0) for link in links),
        "unique_clickers": len(unique_clickers),
        "steps": [
            {
                "id": template_id or campaign_id or job_id,
                "node_id": "",
                "name": step_name,
                "email_template_id": template_id,
                "links": links,
                "documents": [],
            }
        ],
    }


def _merge_chain_template_links(steps: list[dict[str, Any]]) -> None:
    with session_scope() as session:
        for step in steps:
            template_id = _safe_text(step.get("email_template_id"))
            if not template_id:
                continue
            template = session.get(MailTemplate, template_id)
            version = (
                session.get(TemplateVersion, template.active_version_id)
                if template is not None and template.active_version_id
                else None
            )
            if version is None:
                continue
            declared_links = _template_links(
                _safe_text(version.body_html),
                _safe_text(version.body_text),
            )
            links = [
                link for link in step.get("links") or [] if isinstance(link, dict)
            ]
            tracked_by_url = {
                _normalize_url(link.get("url")): link
                for link in links
                if _safe_text(link.get("kind")) == "template"
                and _normalize_url(link.get("url"))
            }
            for declared in declared_links:
                url = declared["url"]
                tracked = tracked_by_url.get(url)
                if tracked is not None:
                    tracked["label"] = declared["label"]
                    tracked["url"] = url
                    continue
                links.append(
                    {
                        "id": "template-" + hashlib.sha256(
                            f"{template_id}:{url}".encode("utf-8")
                        ).hexdigest()[:16],
                        "label": declared["label"],
                        "kind": "template",
                        "url": url,
                        "tokens": 0,
                        "clicks": 0,
                        "unique_clickers": 0,
                        "clickers": [],
                    }
                )
            step["links"] = links


def build_campaign_link_analytics(job_id: str, campaign: dict[str, Any] | None) -> dict[str, Any]:
    if not campaign:
        return _empty_link_analytics()
    if _safe_text(campaign.get("send_scenario")) == "email_chain" or _safe_text(
        campaign.get("email_chain_id")
    ):
        stats = get_chain_click_stats(_safe_text(campaign.get("id")))
        steps = list(stats.get("steps") or [])
        _merge_chain_template_links(steps)
        unique_clickers = {
            _safe_text(clicker.get("recipient_id"))
            for step in steps
            for link in step.get("links") or []
            for clicker in link.get("clickers") or []
            if _safe_text(clicker.get("recipient_id"))
        }
        return {
            "mode": "chain",
            "has_links": any(bool(step.get("links")) for step in steps),
            "has_documents": any(bool(step.get("documents")) for step in steps),
            "total_clicks": sum(
                int(link.get("clicks") or 0)
                for step in steps
                for link in step.get("links") or []
            ),
            "unique_clickers": len(unique_clickers),
            "steps": steps,
        }
    return _standalone_link_analytics(job_id, campaign)
