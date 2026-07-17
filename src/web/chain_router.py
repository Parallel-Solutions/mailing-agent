"""Public endpoints for email-chain branch clicks."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse

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

    @router.get("/chain/branch/{token}", response_class=HTMLResponse)
    def chain_branch_click(token: str, background_tasks: BackgroundTasks):
        try:
            result = record_branch_click(token)
        except ValueError as exc:
            return _page("Ссылка недоступна", str(exc))

        target_node = _resolve_target_node(
            str(result.get("campaign_id") or ""),
            str(result.get("target_node_id") or ""),
        )
        if target_node is None:
            return _page("Ссылка недоступна", "Целевой блок не найден.")

        if is_link_node(target_node):
            link_kind = str(target_node.get("link_kind") or "").strip().lower()
            if link_kind == LINK_KIND_UNSUBSCRIBE:
                with session_scope() as session:
                    recipient = session.get(CampaignRecipient, int(result["recipient_id"]))
                    email = recipient.email if recipient else ""
                if not result.get("already_clicked"):
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
                with session_scope() as session:
                    recipient = session.get(CampaignRecipient, int(result["recipient_id"]))
                    email = recipient.email if recipient else ""
                if not result.get("already_clicked"):
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
            if result.get("send_status") != "sent":
                background_tasks.add_task(dispatch_chain_followup, token)

            if result.get("already_clicked"):
                return _page("Спасибо", "Вы уже перешли по этой ссылке. Следующее письмо будет отправлено на ваш email.")
            return _page("Спасибо", "Мы зафиксировали ваш выбор. Следующее письмо будет отправлено на указанный email.")

        return _page("Ссылка недоступна", "Неизвестный тип блока.")

    return router
