from __future__ import annotations

from html import escape

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse

from src.generator.delivery.consent_store import CONSENT_TEXT, confirm_consent, get_consent_by_token


def _dispatch_materials_after_consent(record: dict) -> None:
    from src.generator.delivery.sender_agent import run_sender

    job_id = str(record.get("job_id") or "").strip() or None
    row_id = str(record.get("row_id") or "").strip()
    transport = str(record.get("transport") or "").strip() or "smtp"
    if not row_id:
        return
    run_sender(
        dry_run=False,
        row_ids=[row_id],
        transport=transport,
        send_mode="materials",
        job_id=job_id,
    )


def create_consent_router() -> APIRouter:
    router = APIRouter()

    @router.get("/consent/request/{token}", response_class=HTMLResponse)
    async def consent_request(token: str):
        record = get_consent_by_token(token)
        if not record:
            return HTMLResponse(
                _render_page(
                    "Ссылка не найдена",
                    "Не удалось найти запрос согласия по этой ссылке. Возможно, ссылка устарела или была скопирована не полностью.",
                ),
                status_code=404,
            )
        if str(record.get("status") or "").strip() == "confirmed":
            return HTMLResponse(
                _render_page(
                    "Согласие уже получено",
                    "Спасибо. Мы уже зафиксировали согласие и направим материалы, если они ещё не были отправлены.",
                )
            )
        return HTMLResponse(_render_consent_form(token, record))

    @router.get("/consent/confirm/{token}", response_class=HTMLResponse)
    async def consent_confirm_get(token: str):
        return await consent_request(token)

    @router.post("/consent/confirm/{token}", response_class=HTMLResponse)
    async def consent_confirm(token: str, request: Request, background_tasks: BackgroundTasks):
        record = confirm_consent(
            token,
            ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
        )
        if not record:
            return HTMLResponse(
                _render_page(
                    "Ссылка не найдена",
                    "Не удалось найти согласие по этой ссылке. Возможно, ссылка устарела или была скопирована не полностью.",
                ),
                status_code=404,
            )
        background_tasks.add_task(_dispatch_materials_after_consent, record)
        return HTMLResponse(
            _render_page(
                "Согласие получено",
                "Спасибо. Мы зафиксировали согласие. Коммерческое предложение и проект договора будут отправлены на указанный адрес.",
            )
        )

    return router


def _render_page(title: str, message: str) -> str:
    safe_title = escape(title)
    safe_message = escape(message)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Arial, sans-serif;
      color: #09210f;
      background: #f7faf3;
    }}
    main {{
      width: min(560px, calc(100vw - 32px));
      padding: 28px;
      border: 1px solid #dbe7d0;
      border-radius: 8px;
      background: white;
      box-shadow: 0 18px 40px rgba(20, 45, 20, .08);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
    }}
    p {{
      margin: 0;
      font-size: 16px;
      line-height: 1.5;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
  </main>
</body>
</html>"""


def _render_consent_form(token: str, record: dict) -> str:
    safe_mun_name = escape(str(record.get("mun_name") or ""))
    safe_recipient = escape(str(record.get("recipient") or ""))
    safe_token = escape(token)
    safe_consent_text = escape(str(record.get("consent_text") or CONSENT_TEXT))
    mun_line = f"<p><strong>Муниципальное образование:</strong> {safe_mun_name}</p>" if safe_mun_name else ""
    recipient_line = f"<p><strong>Email:</strong> {safe_recipient}</p>" if safe_recipient else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Подтверждение запроса материалов</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Arial, sans-serif;
      color: #09210f;
      background: #f7faf3;
    }}
    main {{
      width: min(640px, calc(100vw - 32px));
      padding: 28px;
      border: 1px solid #dbe7d0;
      border-radius: 8px;
      background: white;
      box-shadow: 0 18px 40px rgba(20, 45, 20, .08);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
    }}
    p {{
      margin: 0 0 12px;
      font-size: 16px;
      line-height: 1.5;
    }}
    .consent-text {{
      margin: 16px 0;
      padding: 14px;
      border: 1px solid #e1ead8;
      border-radius: 8px;
      background: #f8fbf4;
      color: #35512b;
    }}
    button {{
      height: 42px;
      padding: 0 18px;
      border: 0;
      border-radius: 8px;
      background: #2d720d;
      color: white;
      font-weight: 700;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Получить предложение по МНГП</h1>
    <p>Подтвердите, что хотите получить коммерческое предложение и проект договора.</p>
    {mun_line}
    {recipient_line}
    <div class="consent-text">{safe_consent_text}</div>
    <form method="post" action="/consent/confirm/{safe_token}">
      <button type="submit">Подтверждаю, направьте материалы</button>
    </form>
  </main>
</body>
</html>"""
