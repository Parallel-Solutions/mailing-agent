"""CampaignFlow /api/v1 endpoints for the new React UI."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from src.campaigns import audience_service, profile_service, service, template_service
from src.campaigns.schedule_planner import plan_batches
from src.jobs.access import coerce_principal
from src.security.auth import Principal
from src.utils.config import settings
from src.web.upload_validation import validate_uploaded_file


class CampaignCreateBody(BaseModel):
    name: str | None = None
    work_type: str | None = None
    document_mode: str | None = None
    mail_subject: str | None = None
    description: str | None = None
    send_scenario: str | None = None
    tags: list[str] | None = None
    internal_comment: str | None = None
    smtp_mailbox_id: str | None = None
    transport: str | None = None
    draft_payload: dict[str, Any] | None = None


class CampaignUpdateBody(BaseModel):
    name: str | None = None
    work_type: str | None = None
    document_mode: str | None = None
    mail_subject: str | None = None
    description: str | None = None
    send_scenario: str | None = None
    tags: list[str] | None = None
    internal_comment: str | None = None
    smtp_mailbox_id: str | None = None
    transport: str | None = None
    email_template_id: str | None = None
    kp_template_id: str | None = None
    contract_template_id: str | None = None
    audience_id: str | None = None
    draft_payload: dict[str, Any] | None = None


class RecipientsReplaceBody(BaseModel):
    recipients: list[dict[str, Any]] = Field(default_factory=list)


class RecipientUpdateBody(BaseModel):
    company: str | None = None
    contact_name: str | None = None
    email: str | None = None
    email_fallback: str | None = None
    region: str | None = None
    source: str | None = None
    excluded: bool | None = None
    extra: dict[str, Any] | None = None


class RecipientsDeleteBody(BaseModel):
    ids: list[int] = Field(default_factory=list)


class ScheduleBody(BaseModel):
    send_immediately: bool | None = None
    start_at: str | None = None
    timezone: str | None = None
    weekdays: list[int] | None = None
    time_windows: list[dict[str, Any]] | None = None
    batch_size: int | None = None
    interval_seconds: int | None = None
    pause_between_messages_ms: int | None = None
    max_per_hour: int | None = None
    max_per_day: int | None = None
    on_error: str | None = None
    max_retries: int | None = None


class SchedulePreviewBody(BaseModel):
    recipient_count: int = 0
    batch_size: int = 25
    interval_seconds: int = 300
    start_at: str | None = None
    send_immediately: bool = True
    timezone: str = "Europe/Moscow"
    weekdays: list[int] = Field(default_factory=list)
    time_windows: list[dict[str, Any]] = Field(default_factory=list)
    max_per_hour: int = 0
    max_per_day: int = 0


class ProfileUpdateBody(BaseModel):
    display_name: str | None = None
    email: str | None = None
    company: str | None = None
    job_title: str | None = None
    signature: str | None = None
    timezone: str | None = None
    mailing_defaults: dict[str, Any] | None = None
    notifications: dict[str, Any] | None = None


class TemplateCreateBody(BaseModel):
    name: str
    template_type: str
    subject: str = ""
    body_html: str = ""
    body_text: str = ""
    tags: list[str] | None = None


class TemplateSaveBody(BaseModel):
    name: str | None = None
    subject: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    variables: list[dict[str, Any]] | None = None


class AudienceCreateBody(BaseModel):
    name: str
    source: str = "manual"


class AudienceMembersBody(BaseModel):
    members: list[dict[str, Any]] = Field(default_factory=list)


class TestEmailBody(BaseModel):
    to_email: str
    smtp_mailbox_id: str | None = None


def _ok(result: Any) -> dict[str, Any]:
    return {"status": "ok", "result": result}


def _actor(principal: object) -> Principal:
    return coerce_principal(principal)


def _binary_response(item: dict[str, Any], *, disposition: str) -> Response:
    filename = str(item["filename"])
    media_type = str(item.get("media_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    return Response(
        content=item["content"],
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def create_v1_router(*, check_auth: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["v1"])

    # --- Profile ---
    @router.get("/profile")
    def get_profile(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(profile_service.get_or_create_profile(actor.username))

    @router.patch("/profile")
    def patch_profile(body: ProfileUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(profile_service.update_profile(actor.username, body.model_dump(exclude_none=True)))

    # --- Campaigns ---
    @router.get("/campaigns/active-sending")
    def get_active_sending(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(service.active_sending(actor.username, is_admin=actor.is_admin))

    @router.get("/campaigns")
    def get_campaigns(
        principal: object = Depends(check_auth),
        status: str | None = None,
        q: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        actor = _actor(principal)
        return _ok(
            service.list_campaigns(
                actor.username,
                is_admin=actor.is_admin,
                status=status,
                q=q,
                limit=limit,
                offset=offset,
            )
        )

    @router.post("/campaigns")
    def post_campaign(body: CampaignCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(service.create_campaign(actor.username, body.model_dump(exclude_none=True)))

    @router.get("/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.get_campaign(campaign_id, actor.username, is_admin=actor.is_admin)
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.patch("/campaigns/{campaign_id}")
    def patch_campaign(campaign_id: str, body: CampaignUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.update_campaign(
            campaign_id, actor.username, body.model_dump(exclude_none=True), is_admin=actor.is_admin
        )
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/duplicate")
    def post_duplicate(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.duplicate_campaign(campaign_id, actor.username, is_admin=actor.is_admin)
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/archive")
    def post_archive(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.archive_campaign(campaign_id, actor.username, is_admin=actor.is_admin)
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.get("/campaigns/{campaign_id}/recipients")
    def get_recipients(
        campaign_id: str,
        principal: object = Depends(check_auth),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        q: str | None = None,
    ):
        actor = _actor(principal)
        return _ok(
            service.list_recipients(
                campaign_id, actor.username, is_admin=actor.is_admin, limit=limit, offset=offset, q=q
            )
        )

    @router.put("/campaigns/{campaign_id}/recipients")
    def put_recipients(campaign_id: str, body: RecipientsReplaceBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                service.replace_recipients(
                    campaign_id, actor.username, body.recipients, is_admin=actor.is_admin
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/campaigns/{campaign_id}/recipients/{recipient_id}")
    def patch_recipient(
        campaign_id: str,
        recipient_id: int,
        body: RecipientUpdateBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        item = service.update_recipient(
            campaign_id,
            recipient_id,
            actor.username,
            body.model_dump(exclude_none=True),
            is_admin=actor.is_admin,
        )
        if not item:
            raise HTTPException(status_code=404, detail="Получатель не найден")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/recipients/delete")
    def post_delete_recipients(
        campaign_id: str, body: RecipientsDeleteBody, principal: object = Depends(check_auth)
    ):
        actor = _actor(principal)
        deleted = service.delete_recipients(
            campaign_id, body.ids, actor.username, is_admin=actor.is_admin
        )
        return _ok({"deleted": deleted})

    @router.post("/campaigns/{campaign_id}/recipients/import")
    def post_import_recipients(
        campaign_id: str,
        principal: object = Depends(check_auth),
        file: UploadFile = File(...),
    ):
        actor = _actor(principal)
        content = file.file.read()
        filename = (file.filename or "").lower()
        try:
            if filename.endswith(".csv"):
                rows = service.parse_recipients_csv(content)
            elif filename.endswith(".xlsx") or filename.endswith(".xlsm"):
                rows = service.parse_recipients_xlsx(content)
            else:
                raise HTTPException(status_code=400, detail="Поддерживаются CSV и XLSX")
            result = service.replace_recipients(
                campaign_id, actor.username, rows, is_admin=actor.is_admin
            )
            return _ok({"import": result, "preview": rows[:20]})
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/schedule")
    def get_schedule(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.get_schedule(campaign_id, actor.username, is_admin=actor.is_admin)
        if not item:
            raise HTTPException(status_code=404, detail="Расписание не найдено")
        return _ok(item)

    @router.put("/campaigns/{campaign_id}/schedule")
    def put_schedule(campaign_id: str, body: ScheduleBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                service.upsert_schedule(
                    campaign_id, actor.username, body.model_dump(exclude_none=True), is_admin=actor.is_admin
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/schedule/preview")
    def post_schedule_preview(body: SchedulePreviewBody, principal: object = Depends(check_auth)):
        _actor(principal)
        start_at = None
        if body.start_at:
            from datetime import datetime

            start_at = datetime.fromisoformat(body.start_at.replace("Z", "+00:00"))
        return _ok(
            plan_batches(
                recipient_count=body.recipient_count,
                batch_size=body.batch_size,
                interval_seconds=body.interval_seconds,
                start_at=start_at,
                send_immediately=body.send_immediately,
                timezone_name=body.timezone,
                weekdays=body.weekdays,
                time_windows=body.time_windows,
                max_per_hour=body.max_per_hour,
                max_per_day=body.max_per_day,
            )
        )

    @router.get("/campaigns/{campaign_id}/validate")
    def get_validate(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(service.validate_campaign_for_launch(campaign_id, actor.username, is_admin=actor.is_admin))

    @router.post("/campaigns/{campaign_id}/launch")
    def post_launch(
        campaign_id: str,
        principal: object = Depends(check_auth),
        force_now: bool = False,
    ):
        actor = _actor(principal)
        try:
            return _ok(
                service.launch_campaign(
                    campaign_id, actor.username, is_admin=actor.is_admin, force_now=force_now
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/pause")
    def post_pause(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(service.pause_campaign(campaign_id, actor.username, is_admin=actor.is_admin))
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/resume")
    def post_resume(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(service.resume_campaign(campaign_id, actor.username, is_admin=actor.is_admin))
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/cancel")
    def post_cancel(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(service.cancel_campaign(campaign_id, actor.username, is_admin=actor.is_admin))
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/batches")
    def get_batches(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(service.list_batches(campaign_id, actor.username, is_admin=actor.is_admin))

    @router.post("/campaigns/{campaign_id}/batches/{batch_id}/cancel")
    def post_cancel_batch(campaign_id: str, batch_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                service.cancel_batch(campaign_id, batch_id, actor.username, is_admin=actor.is_admin)
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/test-email")
    def post_test_email(campaign_id: str, body: TestEmailBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        camp = service.get_campaign(campaign_id, actor.username, is_admin=actor.is_admin)
        if not camp:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        mailbox_id = body.smtp_mailbox_id or camp.get("smtp_mailbox_id")
        if not mailbox_id:
            raise HTTPException(status_code=400, detail="Не выбран SMTP mailbox")
        from src.campaigns.batch_worker import _send_smtp_message

        subject = camp.get("mail_subject") or camp.get("name") or "Тестовое письмо"
        html = str((camp.get("draft_payload") or {}).get("email_body") or f"<p>Тест: {camp.get('name')}</p>")
        try:
            message_id = _send_smtp_message(
                mailbox_id=mailbox_id,
                owner_username=actor.username,
                to_email=body.to_email,
                subject=f"[TEST] {subject}",
                html=html,
                text=html,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось отправить: {exc}") from exc
        return _ok({"message_id": message_id, "to": body.to_email})

    # --- Templates ---
    @router.get("/templates")
    def get_templates(
        principal: object = Depends(check_auth),
        template_type: str | None = None,
        q: str | None = None,
    ):
        actor = _actor(principal)
        return _ok(template_service.list_templates(actor.username, template_type=template_type, q=q))

    @router.post("/templates")
    def post_template(body: TemplateCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(
            template_service.create_template(
                actor.username,
                name=body.name,
                template_type=body.template_type,
                subject=body.subject,
                body_html=body.body_html,
                body_text=body.body_text,
                tags=body.tags,
            )
        )

    @router.post("/templates/upload")
    async def post_template_upload(
        file: UploadFile = File(...),
        template_type: str = Form(...),
        name: str = Form(default=""),
        template_id: str | None = Form(default=None),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        normalized_type = template_type.strip().lower()
        allowed = template_service.FILE_TEMPLATE_EXTENSIONS.get(normalized_type)
        if not allowed:
            raise HTTPException(status_code=400, detail="Файл можно загрузить только для КП или договора")
        original_name = validate_uploaded_file(
            file,
            allowed_extensions=tuple(sorted(allowed)),
            max_bytes=settings.upload_template_max_bytes,
            human_name="шаблона документа",
        )
        data = await file.read()
        try:
            item = template_service.upload_file_version(
                actor.username,
                name=name.strip() or Path(original_name).stem,
                template_type=normalized_type,
                filename=original_name,
                data=data,
                content_type=file.content_type,
                template_id=template_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _ok(item)

    @router.get("/templates/{template_id}/file")
    def get_template_file(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.get_template_file(template_id, actor.username)
        if item is None:
            raise HTTPException(status_code=404, detail="Файл шаблона не найден")
        return _binary_response(item, disposition="attachment")

    @router.get("/templates/{template_id}/preview-file")
    def get_template_preview_file(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = template_service.build_file_preview(template_id, actor.username)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Файл шаблона не найден")
        return _binary_response(item, disposition="inline")
    @router.get("/templates/{template_id}")
    def get_template(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.get_template(template_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    @router.patch("/templates/{template_id}")
    def patch_template(template_id: str, body: TemplateSaveBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.save_version(
            template_id,
            actor.username,
            subject=body.subject,
            body_html=body.body_html,
            body_text=body.body_text,
            variables=body.variables,
            name=body.name,
        )
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    @router.post("/templates/{template_id}/duplicate")
    def post_template_duplicate(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.duplicate_template(template_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    @router.post("/templates/{template_id}/archive")
    def post_template_archive(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.archive_template(template_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    @router.get("/templates/{template_id}/versions")
    def get_template_versions(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(template_service.list_versions(template_id, actor.username))

    @router.post("/templates/{template_id}/preview")
    def post_template_preview(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.preview_template(template_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    # --- Audiences ---
    @router.get("/audiences")
    def get_audiences(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(audience_service.list_audiences(actor.username))

    @router.post("/audiences")
    def post_audience(body: AudienceCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(audience_service.create_audience(actor.username, body.name, source=body.source))

    @router.get("/audiences/{audience_id}")
    def get_audience(audience_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = audience_service.get_audience(audience_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Аудитория не найдена")
        return _ok(item)

    @router.patch("/audiences/{audience_id}")
    def patch_audience(audience_id: str, body: AudienceCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = audience_service.update_audience(
            audience_id, actor.username, {"name": body.name}
        )
        if not item:
            raise HTTPException(status_code=404, detail="Аудитория не найдена")
        return _ok(item)

    @router.post("/audiences/{audience_id}/duplicate")
    def post_audience_duplicate(audience_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = audience_service.duplicate_audience(audience_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Аудитория не найдена")
        return _ok(item)

    @router.get("/audiences/{audience_id}/members")
    def get_audience_members(
        audience_id: str,
        principal: object = Depends(check_auth),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        q: str | None = None,
    ):
        actor = _actor(principal)
        return _ok(
            audience_service.list_members(
                audience_id, actor.username, limit=limit, offset=offset, q=q
            )
        )

    @router.put("/audiences/{audience_id}/members")
    def put_audience_members(
        audience_id: str, body: AudienceMembersBody, principal: object = Depends(check_auth)
    ):
        actor = _actor(principal)
        try:
            return _ok(audience_service.replace_members(audience_id, actor.username, body.members))
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/audiences/{audience_id}/import")
    def post_audience_import(
        audience_id: str,
        principal: object = Depends(check_auth),
        file: UploadFile = File(...),
    ):
        actor = _actor(principal)
        content = file.file.read()
        filename = (file.filename or "").lower()
        if filename.endswith(".csv"):
            rows = service.parse_recipients_csv(content)
        elif filename.endswith(".xlsx") or filename.endswith(".xlsm"):
            rows = service.parse_recipients_xlsx(content)
        else:
            raise HTTPException(status_code=400, detail="Поддерживаются CSV и XLSX")
        try:
            result = audience_service.replace_members(audience_id, actor.username, rows)
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ok({"import": result, "preview": rows[:20]})

    @router.post("/audiences/{audience_id}/use-in-campaign/{campaign_id}")
    def post_use_in_campaign(
        audience_id: str, campaign_id: str, principal: object = Depends(check_auth)
    ):
        actor = _actor(principal)
        try:
            return _ok(audience_service.copy_audience_to_campaign(audience_id, campaign_id, actor.username))
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
