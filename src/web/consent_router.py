from __future__ import annotations

from html import escape

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse

from src.generator.delivery.consent_store import confirm_consent
from src.jobs import load_agent_state, save_agent_state


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _materials_sent_text(record: dict) -> str:
    attachment_mode = _safe_text(record.get("attachment_mode")).lower()
    if attachment_mode == "contract":
        return "Проект договора отправлен."
    if attachment_mode == "both":
        return "КП и проект договора отправлены."
    return "КП отправлено."


def _consent_page_message(record: dict) -> str:
    attachment_mode = _safe_text(record.get("attachment_mode")).lower()
    if attachment_mode == "contract":
        materials = "Проект договора"
    elif attachment_mode == "both":
        materials = "КП и проект договора"
    else:
        materials = "КП"
    return f"Спасибо. Мы получили ваш запрос. {materials} отправим на указанный email отдельным письмом."


def _format_materials_dispatch_summary(record: dict, result: dict) -> str:
    recipient = _safe_text(record.get("recipient"))
    mun_name = _safe_text(record.get("mun_name"))
    target = ", ".join(part for part in (mun_name, recipient) if part)
    sent_rows = int(result.get("sent_rows") or 0)
    error_rows = int(result.get("error_rows") or 0)

    if sent_rows > 0 and error_rows <= 0:
        return (
            f"Клиент дал согласие{f' ({target})' if target else ''}. "
            f"{_materials_sent_text(record)}"
        )
    return (
        f"Клиент дал согласие{f' ({target})' if target else ''}, "
        "но материалы пока не отправились. Проверьте журнал отправки."
    )


def _save_materials_dispatch_summary(record: dict, result: dict) -> None:
    job_id = _safe_text(record.get("job_id")) or None
    summary = _format_materials_dispatch_summary(record, result)
    state = load_agent_state("sender", {}, job_id)
    state["summary_text"] = summary
    save_agent_state("sender", state, job_id)


def _dispatch_materials_after_consent(record: dict) -> None:
    from src.generator.delivery.sender_agent import run_sender

    job_id = str(record.get("job_id") or "").strip() or None
    row_id = str(record.get("row_id") or "").strip()
    transport = str(record.get("transport") or "").strip() or "smtp"
    attachment_mode = str(record.get("attachment_mode") or "").strip() or "kp"
    subject_template = str(record.get("subject_template") or "").strip() or None
    if not row_id:
        return
    result = run_sender(
        dry_run=False,
        row_ids=[row_id],
        transport=transport,
        send_mode="materials",
        attachment_mode=attachment_mode,
        subject_template=subject_template,
        require_confirmed_consent=True,
        job_id=job_id,
    )
    _save_materials_dispatch_summary(record, result)


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
                "Не удалось найти запрос по этой ссылке. Возможно, ссылка устарела или была скопирована не полностью.",
                note="Пожалуйста, вернитесь к письму и попробуйте открыть кнопку ещё раз.",
            ),
            status_code=404,
        )
    background_tasks.add_task(_dispatch_materials_after_consent, record)
    return HTMLResponse(
        _render_page(
            "Запрос получен",
            _consent_page_message(record),
            note="Окно можно закрыть.",
        )
    )


def _render_page(title: str, message: str, *, note: str = "") -> str:
    safe_title = escape(title)
    safe_message = escape(message)
    safe_note = escape(note)
    note_html = f"<p class=\"note\">{safe_note}</p>" if safe_note else ""
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
      padding: 30px;
      border: 1px solid #dbe7d0;
      border-radius: 8px;
      background: white;
      box-shadow: 0 18px 40px rgba(20, 45, 20, .08);
    }}
    .status-mark {{
      width: 42px;
      height: 42px;
      display: grid;
      place-items: center;
      margin-bottom: 18px;
      border-radius: 50%;
      background: #edf7e6;
      color: #2d720d;
      font-size: 24px;
      font-weight: 800;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
      line-height: 1.2;
    }}
    p {{
      margin: 0;
      font-size: 16px;
      line-height: 1.5;
    }}
    .note {{
      margin-top: 16px;
      color: #657260;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <main>
    <div class="status-mark">✓</div>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
    {note_html}
  </main>
</body>
</html>"""
