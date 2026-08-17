"""Public endpoints for email-chain branch clicks."""

from __future__ import annotations

import mimetypes
from html import escape
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError

from src.campaigns.chain_consent_service import record_subscribe, record_unsubscribe
from src.campaigns.chain_send_service import dispatch_chain_followup
from src.campaigns.chain_service import (
    LINK_KIND_CUSTOM,
    LINK_KIND_SUBSCRIBE,
    LINK_KIND_UNSUBSCRIBE,
    find_node,
    get_email_chain,
    is_email_node,
    is_link_node,
    record_branch_click,
    record_tracked_resource_open,
)
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient


def _page(title: str, message: str) -> HTMLResponse:
    safe_title = escape(title)
    safe_message = escape(message)
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{safe_title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #f5f5f5; margin: 0; padding: 40px 16px; }}
    .card {{ max-width: 480px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    h1 {{ font-size: 20px; margin: 0 0 12px; }}
    p {{ margin: 0; color: #444; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


def _resolve_target_node(campaign_id: str, target_node_id: str) -> dict | None:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None:
            return None
        chain = get_email_chain(camp)
        return find_node(chain, target_node_id)


def create_chain_router() -> APIRouter:
    router = APIRouter()

    @router.get("/chain/content/{token}")
    def tracked_content_click(token: str):
        try:
            result = record_tracked_resource_open(token, kind="link")
        except ValueError as exc:
            return _page("Ссылка недоступна", str(exc))
        target_url = str(result.get("target_url") or "").strip()
        if not target_url.lower().startswith(("http://", "https://")):
            return _page("Ссылка недоступна", "URL не настроен.")
        return RedirectResponse(url=target_url, status_code=302)

    @router.get("/chain/document/{token}")
    def tracked_document_open(token: str):
        try:
            result = record_tracked_resource_open(token, kind="document")
        except ValueError as exc:
            return _page("Документ недоступен", str(exc))
        with session_scope() as session:
            campaign = session.get(Campaign, str(result.get("campaign_id") or ""))
            recipient = session.get(
                CampaignRecipient,
                int(result.get("recipient_id") or 0),
            )
            if campaign is None or recipient is None:
                return _page("Документ недоступен", "Получатель или рассылка не найдены.")
            from src.campaigns.template_render_service import resolve_cached_attachment

            resolved = resolve_cached_attachment(
                template_id=str(result.get("template_id") or ""),
                recipient_id=int(recipient.id),
                job_id=campaign.job_id,
                owner_username=campaign.owner_username,
                campaign=campaign,
                recipient=recipient,
            )
        if not resolved:
            return _page("Документ недоступен", "Файл не найден.")
        filename, data = resolved
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return Response(
            content=data,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    "inline; filename*=UTF-8''" + quote(filename)
                ),
            },
        )

    @router.get("/chain/branch/{token}", response_class=HTMLResponse)
    def chain_branch_click(token: str):
        try:
            return _handle_chain_branch_click(token)
        except ValueError as exc:
            return _page("Ссылка недоступна", str(exc))
        except IntegrityError:
            # record_branch_click's clicked_at check-then-set isn't locked,
            # so two concurrent clicks on the same subscribe/unsubscribe
            # link can both see already_clicked=False and both attempt to
            # record consent — the loser hits a uniqueness violation here.
            # That's not a real failure, just a race: treat it the same as
            # "already confirmed" instead of surfacing a 500.
            return _page("Спасибо", "Мы уже зафиксировали ваш выбор по этой ссылке.")

    def _handle_chain_branch_click(token: str) -> HTMLResponse:
        result = record_branch_click(token)

        target_node = _resolve_target_node(
            str(result.get("campaign_id") or ""),
            str(result.get("target_node_id") or ""),
        )
        if target_node is None:
            return _page("Ссылка недоступна", "Целевой блок не найден.")

        if is_link_node(target_node):
            link_kind = str(target_node.get("link_kind") or "").strip().lower()
            is_test = bool(result.get("test_email"))
            if link_kind == LINK_KIND_UNSUBSCRIBE:
                if not is_test and not result.get("already_clicked"):
                    with session_scope() as session:
                        recipient = session.get(CampaignRecipient, int(result["recipient_id"]))
                        email = recipient.email if recipient else ""
                    record_unsubscribe(
                        campaign_id=str(result["campaign_id"]),
                        recipient_id=int(result["recipient_id"]),
                        email=email,
                        node_id=str(result["target_node_id"]),
                        edge_id=str(result["edge_id"]),
                        token=token,
                    )
                return _page("Вы отписаны", "Мы больше не будем отправлять вам письма на указанный email.")

            if link_kind == LINK_KIND_SUBSCRIBE:
                consent_result: dict | None = None
                if not is_test and not result.get("already_clicked"):
                    with session_scope() as session:
                        recipient = session.get(CampaignRecipient, int(result["recipient_id"]))
                        email = recipient.email if recipient else ""
                    consent_result = record_subscribe(
                        campaign_id=str(result["campaign_id"]),
                        recipient_id=int(result["recipient_id"]),
                        email=email,
                        node_id=str(result["target_node_id"]),
                        edge_id=str(result["edge_id"]),
                        token=token,
                    )
                expires_text = ""
                if consent_result and consent_result.get("expires_at"):
                    expires_text = f" Согласие действует до {consent_result['expires_at'][:10]}."
                if result.get("already_clicked"):
                    return _page("Спасибо", "Вы уже подтвердили подписку ранее.")
                return _page(
                    "Спасибо",
                    f"Мы зафиксировали ваше согласие на получение новостей и рекламных рассылок.{expires_text}",
                )

            if link_kind == LINK_KIND_CUSTOM:
                url = str(target_node.get("link_url") or "").strip()
                if not url:
                    return _page("Ссылка недоступна", "URL не настроен.")
                return RedirectResponse(url=url, status_code=302)

            return _page("Ссылка недоступна", "Неизвестный тип ссылки.")

        if is_email_node(target_node):
            if result.get("send_status") not in {"sent", "sending"}:
                dispatch_chain_followup(token)

            if result.get("already_clicked"):
                return _page("Спасибо", "Вы уже перешли по этой ссылке. Следующее письмо будет отправлено на ваш email.")
            return _page("Спасибо", "Мы зафиксировали ваш выбор. Следующее письмо будет отправлено на указанный email.")

        return _page("Ссылка недоступна", "Неизвестный тип блока.")

    return router
