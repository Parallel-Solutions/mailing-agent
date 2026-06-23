from __future__ import annotations

from html import escape

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse

from src.generator.delivery.consent_store import (
    confirm_consent,
    get_consent_by_token,
    mark_materials_dispatch_result,
)
from src.jobs import load_agent_state, save_agent_state
from src.jobs.audit import append_audit_event
from src.security.auth import system_principal


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


def _consent_preview_message(record: dict) -> str:
    attachment_mode = _safe_text(record.get("attachment_mode")).lower()
    if attachment_mode == "contract":
        materials = "проект договора"
    elif attachment_mode == "both":
        materials = "КП и проект договора"
    else:
        materials = "КП"
    recipient = _safe_text(record.get("recipient")) or "указанный email"
    return f"Подтвердите согласие, чтобы мы отправили {materials} на {recipient}."


def _confirmed_page_message(record: dict) -> str:
    if _materials_already_sent(record):
        return f"Согласие уже подтверждено. {_materials_sent_text(record)}"
    if _safe_text(record.get("materials_dispatch_requested_at")):
        return "Согласие уже подтверждено. Материалы уже поставлены в очередь на отправку."
    return _consent_page_message(record)



def _materials_already_sent(record: dict) -> bool:
    return (
        _safe_text(record.get("materials_status")) == "sent"
        or bool(_safe_text(record.get("materials_sent_at")))
    )


def _first_dispatch_row(result: dict) -> dict:
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict):
            return row
    return {}


def _dispatch_error_text(result: dict) -> str:
    row = _first_dispatch_row(result)
    for key in ("error", "warning", "next_action"):
        text = _safe_text(row.get(key))
        if text:
            return text
    return _safe_text(result.get("summary_text")) if isinstance(result, dict) else ""


def _dispatch_was_sent(result: dict) -> bool:
    row = _first_dispatch_row(result)
    row_result = _safe_text(row.get("result"))
    if row_result in {"sent", "skipped_logged_sent"}:
        return True
    if row.get("sent_recipients"):
        return True
    return int(result.get("sent_rows") or 0) > 0 and int(result.get("error_rows") or 0) <= 0


def _format_materials_dispatch_summary(record: dict, result: dict) -> str:
    recipient = _safe_text(record.get("recipient"))
    mun_name = _safe_text(record.get("mun_name"))
    target = ", ".join(part for part in (mun_name, recipient) if part)

    if _dispatch_was_sent(result):
        return (
            f"Клиент дал согласие{f' ({target})' if target else ''}. "
            f"{_materials_sent_text(record)}"
        )
    error_text = _dispatch_error_text(result)
    suffix = f" Причина: {error_text}" if error_text else ""
    return (
        f"Клиент дал согласие{f' ({target})' if target else ''}, "
        f"но материалы пока не отправились. Проверьте журнал отправки.{suffix}"
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
    work_type = str(record.get("work_type") or "").strip() or None
    subject_template = str(record.get("subject_template") or "").strip() or None
    if not row_id:
        return
    if _materials_already_sent(record):
        result = {
            "summary_text": "Материалы уже были отправлены ранее.",
            "sent_rows": 1,
            "error_rows": 0,
            "rows": [
                {
                    "id": row_id,
                    "recipient": _safe_text(record.get("recipient")),
                    "result": "skipped_logged_sent",
                    "error": "",
                    "warning": "",
                }
            ],
        }
        mark_materials_dispatch_result(
            job_id=job_id,
            row_id=row_id,
            recipient=_safe_text(record.get("recipient")),
            sent=True,
            error="",
            summary=_safe_text(result.get("summary_text")),
            attachment_mode=attachment_mode,
        )
        _save_materials_dispatch_summary(record, result)
        return
    result = run_sender(
        dry_run=False,
        row_ids=[row_id],
        transport=transport,
        send_mode="materials",
        attachment_mode=attachment_mode,
        subject_template=subject_template,
        require_confirmed_consent=True,
        work_type=work_type,
        job_id=job_id,
    )
    mark_materials_dispatch_result(
        job_id=job_id,
        row_id=row_id,
        recipient=_safe_text(record.get("recipient")),
        sent=_dispatch_was_sent(result),
        error=_dispatch_error_text(result),
        summary=_safe_text(result.get("summary_text")),
        attachment_mode=attachment_mode,
    )
    _save_materials_dispatch_summary(record, result)


def create_consent_router() -> APIRouter:
    router = APIRouter()

    @router.get("/consent/request/{token}", response_class=HTMLResponse)
    async def consent_request(token: str):
        return _preview_and_render(token)

    @router.get("/consent/confirm/{token}", response_class=HTMLResponse)
    async def consent_confirm_get(token: str, request: Request, background_tasks: BackgroundTasks):
        return await _confirm_and_render(token, request, background_tasks)

    @router.post("/consent/confirm/{token}", response_class=HTMLResponse)
    async def consent_confirm(token: str, request: Request, background_tasks: BackgroundTasks):
        return await _confirm_and_render(token, request, background_tasks)

    return router


def _expired_response(record: dict | None = None) -> HTMLResponse:
    return HTMLResponse(
        _render_page(
            "Ссылка устарела",
            "Срок действия этой ссылки истёк. Пожалуйста, запросите материалы повторно.",
            note="Окно можно закрыть.",
        ),
        status_code=410,
    )


def _audit_consent_confirmation(record: dict, *, status: str, dispatch_materials: bool = False) -> None:
    tenant_id = _safe_text(record.get("tenant_id")) or "public"
    append_audit_event(
        action="consent.confirm",
        principal=system_principal("public-consent", tenant_id=tenant_id),
        job_id=_safe_text(record.get("job_id")) or None,
        status=status,
        details={
            "row_id": _safe_text(record.get("row_id")),
            "recipient": _safe_text(record.get("recipient")),
            "attachment_mode": _safe_text(record.get("attachment_mode")),
            "dispatch_materials": dispatch_materials,
        },
    )

def _not_found_response() -> HTMLResponse:
    return HTMLResponse(
        _render_page(
            "Ссылка не найдена",
            "Не удалось найти запрос по этой ссылке. Возможно, ссылка устарела или была скопирована не полностью.",
            note="Пожалуйста, вернитесь к письму и попробуйте открыть кнопку ещё раз.",
        ),
        status_code=404,
    )


def _preview_and_render(token: str) -> HTMLResponse:
    record = get_consent_by_token(token)
    if not record:
        return _not_found_response()
    if record.get("_expired") or _safe_text(record.get("status")) == "expired":
        return _expired_response(record)
    if _safe_text(record.get("status")) == "confirmed":
        return HTMLResponse(
            _render_page(
                "Согласие уже подтверждено",
                _confirmed_page_message(record),
                note="Окно можно закрыть.",
            )
        )
    return HTMLResponse(
        _render_page(
            "Запрос ожидает подтверждения",
            _consent_preview_message(record),
            note="Для получения материалов откройте ссылку подтверждения из письма.",
        )
    )


async def _confirm_and_render(token: str, request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
    record = confirm_consent(
        token,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    if not record:
        return _not_found_response()
    if record.get("_expired") or _safe_text(record.get("status")) == "expired":
        _audit_consent_confirmation(record, status="expired", dispatch_materials=False)
        return _expired_response(record)
    dispatch_materials = bool(record.get("_dispatch_materials"))
    _audit_consent_confirmation(
        record,
        status="confirmed" if dispatch_materials else "replayed",
        dispatch_materials=dispatch_materials,
    )
    if dispatch_materials:
        background_tasks.add_task(_dispatch_materials_after_consent, record)
    return HTMLResponse(
        _render_page(
            "Запрос получен" if dispatch_materials else "Согласие уже подтверждено",
            _consent_page_message(record) if dispatch_materials else _confirmed_page_message(record),
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
