from __future__ import annotations

from html import escape

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse

from src.generator.delivery.consent_store import confirm_consent


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
    async def consent_request(token: str, request: Request, background_tasks: BackgroundTasks):
        return await _confirm_and_render(token, request, background_tasks)

    @router.get("/consent/confirm/{token}", response_class=HTMLResponse)
    async def consent_confirm_get(token: str, request: Request, background_tasks: BackgroundTasks):
        return await _confirm_and_render(token, request, background_tasks)

    @router.post("/consent/confirm/{token}", response_class=HTMLResponse)
    async def consent_confirm(token: str, request: Request, background_tasks: BackgroundTasks):
        return await _confirm_and_render(token, request, background_tasks)

    return router


async def _confirm_and_render(token: str, request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
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
