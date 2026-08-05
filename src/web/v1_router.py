"""CampaignFlow /api/v1 endpoints for the new React UI."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from src.campaigns import (
    audience_service,
    chain_preview_service,
    chain_service,
    connection_service,
    connection_sender_warmup_service,
    document_layout_service,
    document_editor_service,
    font_service,
    generation_service,
    onboarding_service,
    pdf_overlay_service,
    profile_service,
    sent_email_preview_service,
    service,
    template_ai,
    template_import_service,
    template_service,
    template_preview_image_service,
    template_starters,
    validation_auto_fix_service,
    variable_match_service,
    work_type_service,
)
from src.campaigns.assistants import run_editor_assistant
from src.campaigns.schedule_planner import plan_batches
from src.campaigns.state import CampaignStateConflict
from src.jobs.access import coerce_principal
from src.security.auth import Principal
from src.security.company_access import connection_owner_usernames, visible_owner_usernames
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
    connection_ids: list[str] | None = None
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
    connection_ids: list[str] | None = None
    transport: str | None = None
    email_template_id: str | None = None
    kp_template_id: str | None = None
    contract_template_id: str | None = None
    audience_id: str | None = None
    email_chain_id: str | None = None
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


class VariableMappingSuggestBody(BaseModel):
    model: str | None = None


class VariableMappingSaveBody(BaseModel):
    mapping: dict[str, str] = Field(default_factory=dict)


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


class EmailChainBody(BaseModel):
    version: int = 1
    root_node_id: str
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class ChainCreateBody(BaseModel):
    name: str | None = None


class ChainUpdateBody(BaseModel):
    name: str | None = None


class ProfileUpdateBody(BaseModel):
    display_name: str | None = None
    email: str | None = None
    company: str | None = None
    job_title: str | None = None
    signature: str | None = None
    timezone: str | None = None
    mailing_defaults: dict[str, Any] | None = None
    notifications: dict[str, Any] | None = None


class OnboardingUpdateBody(BaseModel):
    status: Literal["active", "paused", "dismissed", "completed"]
    current_step: int | None = Field(default=None, ge=0, le=100)
    completed_steps: list[str] | None = None


class TemplateCreateBody(BaseModel):
    name: str
    template_type: str
    subject: str = ""
    body_html: str = ""
    body_text: str = ""
    tags: list[str] | None = None
    editor_state: dict[str, Any] | None = None


class TemplateSaveBody(BaseModel):
    name: str | None = None
    subject: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    variables: list[dict[str, Any]] | None = None
    editor_state: dict[str, Any] | None = None
    is_template: bool | None = None
    rendered_pdf_filename: str | None = None
    attachment_output_format: str | None = None


class KpPreviewBody(BaseModel):
    body_html: str


class OfficeSaveBody(BaseModel):
    version_id: str = Field(min_length=1, max_length=200)
    document_key: str = Field(min_length=1, max_length=128)


class PdfOverlayFieldBody(BaseModel):
    id: str
    value: str = ""
    font_size: float | None = None


class PdfOverlaySaveBody(BaseModel):
    fields: list[PdfOverlayFieldBody] = Field(default_factory=list)


class AssistantChatBody(BaseModel):
    editor_kind: str
    resource_id: str
    message: str
    session_id: str | None = None
    model: str | None = None
    snapshot: dict[str, Any] | None = None


class AudienceCreateBody(BaseModel):
    name: str
    source: str = "manual"


class AudienceMembersBody(BaseModel):
    members: list[dict[str, Any]] = Field(default_factory=list)


class TestEmailBody(BaseModel):
    to_email: str
    smtp_mailbox_id: str | None = None


class DocumentLayoutApplyBody(BaseModel):
    template_id: str = Field(min_length=1, max_length=200)


class WorkTypeCreateBody(BaseModel):
    name: str
    mail_subject: str


class ConnectionCreateBody(BaseModel):
    transport: str
    email: str
    sender_name: str = ""
    api_token: str = ""
    sending_key_id: int | None = Field(default=None, ge=1)
    api_base_url: str = ""
    provider: str = "custom"
    password: str = ""
    smtp_username: str = ""
    host: str = ""
    port: int | None = None
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    save_sent_copy: bool = True
    imap_host: str = ""
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool | None = None
    imap_use_starttls: bool | None = None
    imap_username: str = ""
    imap_password: str = ""
    imap_sent_folder: str = ""
    make_default: bool = False
    auth_method: str = "password"
    oauth_provider: str = ""
    oauth_tokens: dict[str, object] | None = None
    max_per_hour: int = 0
    max_per_day: int = 0
    delivery_guard_enabled: bool = False
    delivery_error_rate_threshold: float = 0.05
    delivery_error_window_minutes: int = 60
    delivery_error_min_samples: int = 20
    delivery_error_critical_count: int = 10
    delivery_error_action: str = "warmup"
    delivery_throttled_max_per_hour: int = 50
    warmup_recipients: list[str] = Field(default_factory=list)
    warmup_percent_of_errors: int = 100


class ConnectionUpdateBody(BaseModel):
    transport: str | None = None
    email: str | None = None
    sender_name: str | None = None
    api_token: str | None = None
    sending_key_id: int | None = Field(default=None, ge=1)
    api_base_url: str | None = None
    password: str | None = None
    smtp_username: str | None = None
    host: str | None = None
    port: int | None = None
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    save_sent_copy: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool | None = None
    imap_use_starttls: bool | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    imap_sent_folder: str | None = None
    max_per_hour: int | None = None
    max_per_day: int | None = None
    delivery_guard_enabled: bool | None = None
    delivery_error_rate_threshold: float | None = None
    delivery_error_window_minutes: int | None = None
    delivery_error_min_samples: int | None = None
    delivery_error_critical_count: int | None = None
    delivery_error_action: str | None = None
    delivery_throttled_max_per_hour: int | None = None
    warmup_recipients: list[str] | None = None
    warmup_percent_of_errors: int | None = None


class ConnectionWarmupSettingsBody(BaseModel):
    smtp_connection_id: str | None = None
    timezone: str | None = None
    daily_start_time: str | None = None
    daily_end_time: str | None = None
    pause_campaigns_during_warmup: bool | None = None
    subject_templates: list[str] | None = None
    body_templates: list[str] | None = None
    max_growth_percent: int | None = Field(default=None, ge=20, le=30)
    recipients_consent_confirmed: bool | None = None


class ConnectionWarmupRecipientsBody(BaseModel):
    emails: list[str] = Field(default_factory=list, max_length=500)


class ConnectionWarmupRecipientStatusBody(BaseModel):
    status: Literal["active", "disabled"]


class ConnectionWarmupDiagnosticsBody(BaseModel):
    headers: str = Field(default="", max_length=200_000)

def _ok(result: Any) -> dict[str, Any]:
    return {"status": "ok", "result": result}


def _campaign_conflict(exc: CampaignStateConflict) -> HTTPException:
    return HTTPException(status_code=409, detail=exc.as_detail())


def _actor(principal: object) -> Principal:
    return coerce_principal(principal)


def _visibility(actor: Principal) -> frozenset[str] | None:
    return visible_owner_usernames(actor)


def _connection_visibility(actor: Principal) -> frozenset[str] | None:
    return connection_owner_usernames(actor)


def _binary_response(item: dict[str, Any], *, disposition: str) -> Response:
    filename = str(item["filename"])
    media_type = str(item.get("media_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    return Response(
        content=item["content"],
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


def _preview_image_response(item: dict[str, Any], *, cache_control: str) -> Response:
    headers = {
        "Content-Disposition": "inline; filename*=UTF-8''preview.png",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": cache_control,
    }
    etag = item.get("etag")
    if etag:
        headers["ETag"] = f'"{etag}"'
    return Response(content=item["content"], media_type="image/png", headers=headers)


def create_v1_router(*, check_auth: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["v1"])

    # --- Delivery connections ---
    @router.get("/connections")
    def get_connections(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(connection_service.list_connections(actor.username, visible_owners=_connection_visibility(actor)))

    @router.post("/connections")
    def post_connection(body: ConnectionCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(connection_service.create_connection(actor.username, body.model_dump()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось сохранить подключение: {exc}") from exc

    @router.patch("/connections/{connection_id}")
    def patch_connection(
        connection_id: str,
        body: ConnectionUpdateBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(connection_service.update_connection(
                connection_id, actor.username, body.model_dump(exclude_none=True),
                visible_owners=_connection_visibility(actor),
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/connections/{connection_id}")
    def delete_connection(connection_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            connection_service.delete_connection(connection_id, actor.username, visible_owners=_connection_visibility(actor))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ok({"deleted": True})

    @router.post("/connections/{connection_id}/test")
    def test_connection(connection_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(connection_service.test_connection(connection_id, actor.username, visible_owners=_connection_visibility(actor)))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Проверка подключения не пройдена: {exc}") from exc

    @router.post("/connections/{connection_id}/guard/reset")
    def reset_connection_guard(connection_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                connection_service.reset_connection_guard(
                    connection_id,
                    actor.username,
                    visible_owners=_connection_visibility(actor),
                )
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/connections/{connection_id}/sender-warmup")
    def get_connection_sender_warmup(connection_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(connection_sender_warmup_service.get_program(
                connection_id, actor.username, visible_owners=_connection_visibility(actor)
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/connections/{connection_id}/sender-warmup")
    def patch_connection_sender_warmup(
        connection_id: str,
        body: ConnectionWarmupSettingsBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(connection_sender_warmup_service.update_program(
                connection_id,
                actor.username,
                body.model_dump(exclude_none=True),
                visible_owners=_connection_visibility(actor),
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/connections/{connection_id}/sender-warmup/diagnostics")
    def diagnose_connection_sender_warmup(
        connection_id: str,
        body: ConnectionWarmupDiagnosticsBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(connection_sender_warmup_service.run_diagnostics(
                connection_id,
                actor.username,
                headers=body.headers,
                visible_owners=_connection_visibility(actor),
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/connections/{connection_id}/sender-warmup/recipients")
    def add_connection_sender_warmup_recipients(
        connection_id: str,
        body: ConnectionWarmupRecipientsBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(connection_sender_warmup_service.add_recipients(
                connection_id, actor.username, body.emails, visible_owners=_connection_visibility(actor)
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/connections/{connection_id}/sender-warmup/recipients/{recipient_id}")
    def patch_connection_sender_warmup_recipient(
        connection_id: str,
        recipient_id: str,
        body: ConnectionWarmupRecipientStatusBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(connection_sender_warmup_service.set_recipient_status(
                connection_id,
                recipient_id,
                actor.username,
                body.status,
                visible_owners=_connection_visibility(actor),
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/connections/{connection_id}/sender-warmup/recipients/{recipient_id}")
    def delete_connection_sender_warmup_recipient(
        connection_id: str,
        recipient_id: str,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(connection_sender_warmup_service.set_recipient_status(
                connection_id,
                recipient_id,
                actor.username,
                "removed",
                visible_owners=_connection_visibility(actor),
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/connections/{connection_id}/sender-warmup/{action}")
    def change_connection_sender_warmup_status(
        connection_id: str,
        action: Literal["start", "pause", "resume", "stop"],
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        handlers = {
            "start": connection_sender_warmup_service.start_program,
            "pause": connection_sender_warmup_service.pause_program,
            "resume": connection_sender_warmup_service.resume_program,
            "stop": connection_sender_warmup_service.stop_program,
        }
        try:
            return _ok(handlers[action](
                connection_id, actor.username, visible_owners=_connection_visibility(actor)
            ))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    # --- Profile ---
    @router.get("/profile")
    def get_profile(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(profile_service.get_or_create_profile(actor.username))

    @router.patch("/profile")
    def patch_profile(body: ProfileUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(profile_service.update_profile(actor.username, body.model_dump(exclude_none=True)))

    # --- Onboarding ---
    @router.get("/onboarding")
    def get_onboarding(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(onboarding_service.get_onboarding(actor.username))

    @router.patch("/onboarding")
    def patch_onboarding(body: OnboardingUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                onboarding_service.update_onboarding(actor.username, body.model_dump(exclude_none=True))
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/onboarding/restart")
    def restart_onboarding(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(onboarding_service.restart_onboarding(actor.username))

    # --- Work types ---
    @router.get("/work-types")
    def get_work_types(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(work_type_service.list_work_types(actor.username))

    @router.post("/work-types")
    def post_work_type(body: WorkTypeCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                work_type_service.create_work_type(
                    actor.username,
                    name=body.name,
                    mail_subject=body.mail_subject,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- Campaigns ---
    @router.get("/campaigns/active-sending")
    def get_active_sending(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(service.active_sending(actor.username, visible_owners=_visibility(actor)))

    @router.get("/campaigns")
    def get_campaigns(
        principal: object = Depends(check_auth),
        scope: Literal["all", "draft", "launched"] = "all",
        status: str | None = None,
        q: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        actor = _actor(principal)
        return _ok(
            service.list_campaigns(
                actor.username,
                visible_owners=_visibility(actor),
                scope=scope,
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
        item = service.get_campaign(campaign_id, actor.username, visible_owners=_visibility(actor))
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.patch("/campaigns/{campaign_id}")
    def patch_campaign(campaign_id: str, body: CampaignUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = service.update_campaign(
                campaign_id, actor.username, body.model_dump(exclude_none=True), visible_owners=_visibility(actor)
            )
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/duplicate")
    def post_duplicate(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.duplicate_campaign(campaign_id, actor.username, visible_owners=_visibility(actor))
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/archive")
    def post_archive(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = service.archive_campaign(campaign_id, actor.username, visible_owners=_visibility(actor))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/reset")
    def post_reset_campaign(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = service.reset_campaign_draft(campaign_id, actor.username, visible_owners=_visibility(actor))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
                campaign_id, actor.username, visible_owners=_visibility(actor), limit=limit, offset=offset, q=q
            )
        )

    @router.put("/campaigns/{campaign_id}/recipients")
    def put_recipients(campaign_id: str, body: RecipientsReplaceBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                service.replace_recipients(
                    campaign_id, actor.username, body.recipients, visible_owners=_visibility(actor)
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc
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
        try:
            item = service.update_recipient(
                campaign_id,
                recipient_id,
                actor.username,
                body.model_dump(exclude_none=True),
                visible_owners=_visibility(actor),
            )
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Получатель не найден")
        return _ok(item)

    @router.post("/campaigns/{campaign_id}/recipients/delete")
    def post_delete_recipients(
        campaign_id: str, body: RecipientsDeleteBody, principal: object = Depends(check_auth)
    ):
        actor = _actor(principal)
        try:
            deleted = service.delete_recipients(
                campaign_id, body.ids, actor.username, visible_owners=_visibility(actor)
            )
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc
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
                rows, columns = service.parse_recipients_csv(content)
            elif filename.endswith(".xlsx") or filename.endswith(".xlsm"):
                rows, columns = service.parse_recipients_xlsx(content)
            else:
                raise HTTPException(status_code=400, detail="Поддерживаются CSV и XLSX")
            result = service.replace_recipients(
                campaign_id,
                actor.username,
                rows,
                visible_owners=_visibility(actor),
                recipient_columns=columns,
            )
            return _ok({"import": result, "preview": rows[:20]})
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/schedule")
    def get_schedule(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = service.get_schedule(campaign_id, actor.username, visible_owners=_visibility(actor))
        if not item:
            raise HTTPException(status_code=404, detail="Расписание не найдено")
        return _ok(item)

    @router.put("/campaigns/{campaign_id}/schedule")
    def put_schedule(campaign_id: str, body: ScheduleBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                service.upsert_schedule(
                    campaign_id, actor.username, body.model_dump(exclude_none=True), visible_owners=_visibility(actor)
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc

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
    def get_validate(
        campaign_id: str,
        principal: object = Depends(check_auth),
        deep: bool = Query(default=False),
    ):
        actor = _actor(principal)
        return _ok(
            service.validate_campaign_for_launch(
                campaign_id,
                actor.username,
                visible_owners=_visibility(actor),
                deep=deep,
            )
        )

    @router.post("/campaigns/{campaign_id}/validation/auto-fix")
    def post_validation_auto_fix(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                validation_auto_fix_service.auto_fix_campaign_validation(
                    campaign_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/generation")
    def get_campaign_generation(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                generation_service.generation_status(
                    campaign_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/generation/prepare")
    def post_campaign_generation_prepare(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                generation_service.prepare_campaign_generation(
                    campaign_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/chains")
    def get_chains(
        principal: object = Depends(check_auth),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        actor = _actor(principal)
        return _ok(
            chain_service.list_chains(actor.username, visible_owners=_visibility(actor), limit=limit, offset=offset)
        )

    @router.post("/chains")
    def post_chain(body: ChainCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(chain_service.create_chain(actor.username, name=body.name))

    @router.get("/chains/{chain_id}")
    def get_chain(chain_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(chain_service.load_chain(chain_id, actor.username, visible_owners=_visibility(actor)))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/chains/{chain_id}")
    def put_chain(chain_id: str, body: EmailChainBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                chain_service.save_chain(
                    chain_id,
                    actor.username,
                    body.model_dump(),
                    visible_owners=_visibility(actor),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/chains/{chain_id}")
    def patch_chain(chain_id: str, body: ChainUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                chain_service.update_chain(
                    chain_id,
                    actor.username,
                    name=body.name,
                    visible_owners=_visibility(actor),
                )
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if detail == "Цепочка не найдена" else 400
            raise HTTPException(status_code=status, detail=detail) from exc

    @router.delete("/chains/{chain_id}")
    def delete_chain(chain_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            chain_service.delete_chain(
                chain_id,
                actor.username,
                visible_owners=_visibility(actor),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ok({"deleted": True, "id": chain_id})

    @router.post("/chains/{chain_id}/publish")
    def post_chain_publish(chain_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(chain_service.publish_chain(chain_id, actor.username, visible_owners=_visibility(actor)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/email-chain")
    def get_email_chain(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(chain_service.load_email_chain(campaign_id, actor.username, visible_owners=_visibility(actor)))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/campaigns/{campaign_id}/email-chain")
    def put_email_chain(campaign_id: str, body: EmailChainBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                chain_service.save_email_chain(
                    campaign_id,
                    actor.username,
                    body.model_dump(),
                    visible_owners=_visibility(actor),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/email-chain/publish")
    def post_email_chain_publish(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                chain_service.publish_email_chain(campaign_id, actor.username, visible_owners=_visibility(actor))
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/email-chain/stats")
    def get_email_chain_stats(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            chain_service.load_email_chain(campaign_id, actor.username, visible_owners=_visibility(actor))
            return _ok(chain_service.get_chain_click_stats(campaign_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/email-chain/preview")
    def post_email_chain_preview(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                chain_preview_service.preview_chain_for_campaign(
                    campaign_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                )
            )
        except ValueError as exc:
            message = str(exc)
            status = 404 if "не найден" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @router.post("/campaigns/{campaign_id}/document-layout/inspect")
    def post_campaign_document_layout_inspect(
        campaign_id: str,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(
                document_layout_service.inspect_campaign_layout(
                    campaign_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                )
            )
        except ValueError as exc:
            message = str(exc)
            status = 404 if "не найден" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @router.post("/campaigns/{campaign_id}/document-layout/apply")
    def post_campaign_document_layout_apply(
        campaign_id: str,
        body: DocumentLayoutApplyBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(
                document_layout_service.apply_campaign_layout(
                    campaign_id,
                    body.template_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                )
            )
        except pdf_overlay_service.PdfOverlayLayoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            status = 404 if "не найден" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @router.get("/campaigns/{campaign_id}/email-chain/preview/attachment")
    def get_email_chain_preview_attachment(
        campaign_id: str,
        principal: object = Depends(check_auth),
        recipient_id: int = Query(..., ge=1),
        template_id: str = Query(..., min_length=1),
        download: int = Query(0, ge=0, le=1),
        preview: int = Query(0, ge=0, le=1),
    ):
        actor = _actor(principal)
        try:
            resolved = chain_preview_service.resolve_preview_attachment(
                campaign_id,
                recipient_id,
                template_id,
                actor.username,
                as_pdf=bool(preview),
                visible_owners=_visibility(actor),
            )
        except template_service.DocumentConversionError as exc:
            raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if resolved is None:
            raise HTTPException(status_code=404, detail="Вложение не найдено")
        filename, content = resolved
        disposition = "attachment" if download else "inline"
        return _binary_response(
            {"filename": filename, "content": content},
            disposition=disposition,
        )

    @router.get("/campaigns/{campaign_id}/sent-email-preview")
    def get_sent_email_preview(
        campaign_id: str,
        principal: object = Depends(check_auth),
        recipient_id: int = Query(..., ge=1),
    ):
        actor = _actor(principal)
        try:
            return _ok(
                sent_email_preview_service.preview_sent_email_for_recipient(
                    campaign_id,
                    actor.username,
                    recipient_id=recipient_id,
                    visible_owners=_visibility(actor),
                )
            )
        except ValueError as exc:
            message = str(exc)
            status = 404 if "не найден" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @router.get("/campaigns/{campaign_id}/variable-mapping")
    def get_variable_mapping(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                variable_match_service.get_variable_mapping_state(
                    campaign_id, actor.username, visible_owners=_visibility(actor)
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/variable-mapping/suggest")
    def post_variable_mapping_suggest(
        campaign_id: str,
        body: VariableMappingSuggestBody | None = None,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(
                variable_match_service.suggest_variable_mapping(
                    campaign_id,
                    actor.username,
                    visible_owners=_visibility(actor),
                    model=(body.model if body else None) or "",
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/campaigns/{campaign_id}/variable-mapping")
    def put_variable_mapping(
        campaign_id: str,
        body: VariableMappingSaveBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            return _ok(
                variable_match_service.save_variable_mapping(
                    campaign_id,
                    actor.username,
                    body.mapping,
                    visible_owners=_visibility(actor),
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                    campaign_id, actor.username, visible_owners=_visibility(actor), force_now=force_now
                )
            )
        except CampaignStateConflict as exc:
            raise _campaign_conflict(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/pause")
    def post_pause(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(service.pause_campaign(campaign_id, actor.username, visible_owners=_visibility(actor)))
        except CampaignStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/resume")
    def post_resume(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(service.resume_campaign(campaign_id, actor.username, visible_owners=_visibility(actor)))
        except CampaignStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/cancel")
    def post_cancel(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(service.cancel_campaign(campaign_id, actor.username, visible_owners=_visibility(actor)))
        except CampaignStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/batches")
    def get_batches(campaign_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(service.list_batches(campaign_id, actor.username, visible_owners=_visibility(actor)))

    @router.post("/campaigns/{campaign_id}/batches/{batch_id}/cancel")
    def post_cancel_batch(campaign_id: str, batch_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                service.cancel_batch(campaign_id, batch_id, actor.username, visible_owners=_visibility(actor))
            )
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/test-email")
    def post_test_email(campaign_id: str, body: TestEmailBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        validation = service.validate_campaign_for_launch(
            campaign_id,
            actor.username,
            visible_owners=_visibility(actor),
            deep=False,
        )
        if not validation.get("ok"):
            raise HTTPException(status_code=400, detail="; ".join(validation.get("errors") or []))
        camp = service.get_campaign(campaign_id, actor.username, visible_owners=_visibility(actor))
        if not camp:
            raise HTTPException(status_code=404, detail="Рассылка не найдена")
        connection_id = body.smtp_mailbox_id or camp.get("smtp_mailbox_id")
        if not connection_id:
            raise HTTPException(status_code=400, detail="Не выбрано подключение отправителя")

        if camp.get("send_scenario") == "email_chain":
            from src.campaigns.chain_send_service import start_test_chain

            try:
                result = start_test_chain(
                    campaign_id,
                    body.to_email,
                    actor.username,
                    str(connection_id),
                    visible_owners=_visibility(actor),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Не удалось отправить: {exc}") from exc
            return _ok(result)

        from src.campaigns.batch_worker import _send_delivery_message
        from src.campaigns.recipient_email_service import validate_delivery_email
        from src.infra.db import session_scope
        from src.infra.models import MailTemplate, TemplateVersion

        email_validation = validate_delivery_email(body.to_email)
        if not email_validation.is_valid:
            raise HTTPException(
                status_code=400,
                detail=email_validation.reason or "Email не прошёл проверку SMTP.BZ.",
            )
        delivery_email = email_validation.normalized_email

        subject = camp.get("mail_subject") or camp.get("name") or "Тестовое письмо"
        html = str((camp.get("draft_payload") or {}).get("email_body") or f"<p>Тест: {camp.get('name')}</p>")
        email_template_id = camp.get("email_template_id")
        if email_template_id:
            with session_scope() as session:
                tmpl = session.get(MailTemplate, email_template_id)
                if tmpl and tmpl.active_version_id:
                    version = session.get(TemplateVersion, tmpl.active_version_id)
                    if version:
                        subject = version.subject or subject
                        html = version.body_html or html
        try:
            message_id = _send_delivery_message(
                connection_id=connection_id,
                owner_username=actor.username,
                to_email=delivery_email,
                subject=f"[TEST] {subject}",
                html=html,
                text=html,
                job_id=camp.get("job_id"),
                row_id="campaign-test",
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось отправить: {exc}") from exc
        return _ok({"message_id": message_id, "to": delivery_email})

    # --- Editor assistants ---
    @router.post("/assistants/chat")
    def post_assistant_chat(body: AssistantChatBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                run_editor_assistant(
                    editor_kind=body.editor_kind,
                    resource_id=body.resource_id,
                    message=body.message,
                    owner_username=actor.username,
                    is_admin=actor.is_admin,
                    session_id=body.session_id,
                    model=body.model,
                    snapshot=body.snapshot,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Ассистент недоступен: {exc}") from exc

    # --- Templates ---
    @router.get("/fonts")
    def get_fonts(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(font_service.list_fonts(actor.username))

    @router.post("/fonts/upload")
    def post_font_upload(
        file: UploadFile = File(...),
        license_confirmed: bool = Form(default=False),
        template_id: str | None = Form(default=None),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        original_name = validate_uploaded_file(
            file,
            allowed_extensions=(".ttf", ".otf"),
            max_bytes=settings.upload_font_max_bytes,
            human_name="шрифта",
        )
        try:
            item = font_service.upload_font(
                actor.username,
                filename=original_name,
                data=file.file.read(),
                license_confirmed=license_confirmed,
                created_by=actor.username,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        affected_ids = set(
            font_service.template_ids_requiring_family(
                actor.username,
                str(item.get("family_normalized") or ""),
            )
        )
        if template_id:
            affected_ids.add(template_id)
        for affected_id in affected_ids:
            template_service.invalidate_template_font_cache(affected_id, actor.username)
        return _ok(item)

    @router.delete("/fonts/{font_id}")
    def delete_font(
        font_id: str,
        template_id: str | None = Query(default=None),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        fonts = font_service.list_fonts(actor.username)
        font = next((item for item in fonts if item["id"] == font_id), None)
        if font is None:
            raise HTTPException(status_code=404, detail="Шрифт не найден")
        affected_ids = set(
            font_service.template_ids_requiring_family(
                actor.username,
                str(font.get("family_normalized") or ""),
            )
        )
        if template_id:
            affected_ids.add(template_id)
        if not font_service.delete_font(actor.username, font_id):
            raise HTTPException(status_code=404, detail="Шрифт не найден")
        for affected_id in affected_ids:
            template_service.invalidate_template_font_cache(affected_id, actor.username)
        return _ok({"deleted": True, "id": font_id})

    @router.get("/templates/{template_id}/fonts")
    def get_template_fonts(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(font_service.get_template_fonts(template_id, actor.username))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/templates/{template_id}/fonts/resolve")
    def post_template_fonts_resolve(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            result = font_service.resolve_template_fonts(template_id, actor.username)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        template_service.invalidate_template_font_cache(template_id, actor.username)
        return _ok(result)

    @router.get("/templates")
    def get_templates(
        principal: object = Depends(check_auth),
        template_type: str | None = None,
        q: str | None = None,
    ):
        actor = _actor(principal)
        return _ok(template_service.list_templates(actor.username, template_type=template_type, q=q, visible_owners=_visibility(actor)))

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
                editor_state=body.editor_state,
            )
        )

    @router.post("/templates/upload")
    def post_template_upload(
        file: UploadFile = File(...),
        template_type: str = Form(...),
        name: str = Form(default=""),
        template_id: str | None = Form(default=None),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        normalized_type = template_service.normalize_file_template_type(template_type)
        allowed = template_service.FILE_TEMPLATE_EXTENSIONS.get(normalized_type)
        if not allowed:
            raise HTTPException(status_code=400, detail="Файл можно загрузить только для документов")
        original_name = validate_uploaded_file(
            file,
            allowed_extensions=tuple(sorted(allowed)),
            max_bytes=settings.upload_template_max_bytes,
            human_name="шаблона документа",
        )
        data = file.file.read()
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
        except template_service.DocumentConversionError as exc:
            raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _ok(item)

    @router.get("/templates/starters")
    def get_template_starters(
        principal: object = Depends(check_auth),
        template_type: str | None = None,
    ):
        _actor(principal)
        return _ok(template_starters.list_starters(template_type=template_type))

    @router.get("/templates/starters/{starter_id}/preview-image")
    def get_template_starter_preview_image(starter_id: str, principal: object = Depends(check_auth)):
        _actor(principal)
        try:
            item = template_preview_image_service.get_starter_preview_image(starter_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Превью примера не найдено")
        return _preview_image_response(item, cache_control="public, max-age=86400")

    @router.post("/templates/starters/{starter_id}/use")
    def post_template_starter_use(starter_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = template_starters.use_starter(actor.username, starter_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ok(item)

    @router.get("/templates/models")
    def get_template_models(principal: object = Depends(check_auth)):
        _actor(principal)
        return _ok(template_ai.list_models())

    @router.post("/templates/generate")
    def post_template_generate(
        template_type: str = Form(...),
        prompt: str = Form(default=""),
        model: str = Form(default=""),
        files: list[UploadFile] | None = File(default=None),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        attachments: list[tuple[str, bytes]] = []
        for upload in files or []:
            if not upload.filename:
                continue
            original_name = validate_uploaded_file(
                upload,
                allowed_extensions=(".docx", ".pdf", ".html", ".htm", ".txt"),
                max_bytes=settings.upload_template_max_bytes,
                human_name="вложения шаблона",
            )
            attachments.append((original_name, upload.file.read()))
        try:
            item = template_ai.generate_template(
                actor.username,
                template_type=template_type,
                prompt=prompt,
                model=model,
                files=attachments,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _ok(item)

    @router.post("/templates/import")
    def post_template_import(
        file: UploadFile = File(...),
        template_type: str = Form(default="email"),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        normalized_type = template_service.normalize_file_template_type(template_type)
        if normalized_type != "email":
            raise HTTPException(status_code=400, detail="Импорт доступен только для шаблонов письма")
        original_name = validate_uploaded_file(
            file,
            allowed_extensions=(".docx", ".pdf", ".html", ".htm", ".txt"),
            max_bytes=settings.upload_template_max_bytes,
            human_name="шаблона письма",
        )
        data = file.file.read()
        try:
            item = template_import_service.import_visual_email_template(
                actor.username,
                filename=original_name,
                data=data,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _ok(item)

    @router.post("/templates/{template_id}/import-regenerate")
    def post_template_import_regenerate(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = template_import_service.regenerate_imported_template(actor.username, template_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _ok(item)

    @router.get("/templates/{template_id}/file")
    def get_template_file(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.get_template_file(template_id, actor.username)
        if item is None:
            raise HTTPException(status_code=404, detail="Файл шаблона не найден")
        return _binary_response(item, disposition="attachment")

    @router.get("/templates/{template_id}/delivery-file")
    def get_template_delivery_file(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = template_service.get_template_delivery_file(template_id, actor.username)
        except template_service.DocumentConversionError as exc:
            raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="PDF для отправки не найден")
        return _binary_response(item, disposition="attachment")

    @router.get("/templates/{template_id}/preview-image")
    def get_template_preview_image(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = template_preview_image_service.get_template_preview_image(template_id, actor.username)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Превью шаблона не найдено")
        return _preview_image_response(item, cache_control="private, max-age=3600")

    @router.get("/templates/{template_id}/preview-file")
    def get_template_preview_file(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = template_service.build_file_preview(template_id, actor.username)
        except template_service.DocumentConversionError as exc:
            raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Файл шаблона не найден")
        return _binary_response(item, disposition="inline")

    @router.get("/templates/{template_id}/pdf-editor")
    def get_pdf_editor(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(pdf_overlay_service.get_editor_state(template_id, actor.username))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/templates/{template_id}/pdf-editor/pages/{page_index}")
    def get_pdf_editor_page(template_id: str, page_index: int, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = pdf_overlay_service.render_source_page(template_id, actor.username, page_index)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _binary_response(item, disposition="inline")

    @router.patch("/templates/{template_id}/pdf-editor")
    def patch_pdf_editor(template_id: str, body: PdfOverlaySaveBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            item = pdf_overlay_service.save_editor_state(
                template_id,
                actor.username,
                [field.model_dump(exclude_none=True) for field in body.fields],
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _ok(item)
    @router.post("/templates/{template_id}/kp-preview-file")
    def post_kp_preview_file(
        template_id: str,
        body: KpPreviewBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        try:
            item = template_service.build_kp_pdf_preview(
                template_id,
                actor.username,
                body_html=body.body_html,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _binary_response(item, disposition="inline")

    @router.get("/templates/{template_id}/office-config")
    def get_office_config(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(document_editor_service.editor_config(template_id, actor.username))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/templates/{template_id}/office-source")
    def get_office_source(template_id: str, token: str = Query(...)):
        try:
            item = document_editor_service.source_file(template_id, token)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _binary_response(item, disposition="inline")

    @router.post("/templates/{template_id}/office-callback")
    def post_office_callback(template_id: str, payload: dict[str, Any], token: str = Query(...)):
        try:
            return document_editor_service.handle_callback(template_id, token, payload)
        except PermissionError:
            return {"error": 1}

    @router.post("/templates/{template_id}/office-save")
    def post_office_save(template_id: str, body: OfficeSaveBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        try:
            return _ok(
                document_editor_service.force_save(
                    template_id,
                    actor.username,
                    body.version_id,
                    body.document_key,
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


    @router.get("/templates/{template_id}")
    def get_template(template_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.get_template(template_id, actor.username, visible_owners=_visibility(actor))
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    @router.patch("/templates/{template_id}")
    def patch_template(template_id: str, body: TemplateSaveBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        current = template_service.get_template(template_id, actor.username, visible_owners=_visibility(actor))
        if current and current.get("template_type") == "kp" and body.body_html is not None:
            try:
                item = template_service.save_kp_html_version(
                    template_id,
                    actor.username,
                    body_html=body.body_html,
                    name=body.name,
                )
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            try:
                item = template_service.save_version(
                    template_id,
                    actor.username,
                    subject=body.subject,
                    body_html=body.body_html,
                    body_text=body.body_text,
                    variables=body.variables,
                    name=body.name,
                    editor_state=body.editor_state,
                    is_template=body.is_template,
                    rendered_pdf_filename=body.rendered_pdf_filename,
                    attachment_output_format=body.attachment_output_format,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok(item)

    @router.post("/templates/{template_id}/assets")
    def post_template_asset(
        template_id: str,
        file: UploadFile = File(...),
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        filename = Path(file.filename or "").name
        if not filename:
            raise HTTPException(status_code=400, detail="Не удалось определить имя файла изображения.")
        suffix = Path(filename).suffix.lower()
        if suffix not in template_service.EMAIL_ASSET_EXTENSIONS:
            allowed = ", ".join(sorted(template_service.EMAIL_ASSET_EXTENSIONS))
            raise HTTPException(status_code=400, detail=f"Поддерживаются только изображения: {allowed}.")
        data = file.file.read()
        if len(data) > settings.upload_template_max_bytes:
            raise HTTPException(status_code=400, detail="Файл изображения слишком большой.")
        try:
            item = template_service.upload_template_asset(
                template_id,
                actor.username,
                filename=filename,
                data=data,
                content_type=file.content_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _ok({"data": [{"type": "image", "src": item["url"]}]})

    @router.get("/templates/{template_id}/assets/{asset_id}")
    def get_template_asset(template_id: str, asset_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = template_service.get_template_asset(template_id, asset_id, actor.username)
        if not item:
            raise HTTPException(status_code=404, detail="Изображение не найдено")
        return Response(content=item["content"], media_type=item["content_type"])

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
        return _ok(audience_service.list_audiences(actor.username, visible_owners=_visibility(actor)))

    @router.post("/audiences")
    def post_audience(body: AudienceCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        return _ok(audience_service.create_audience(actor.username, body.name, source=body.source))

    @router.get("/audiences/{audience_id}")
    def get_audience(audience_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = audience_service.get_audience(audience_id, actor.username, visible_owners=_visibility(actor))
        if not item:
            raise HTTPException(status_code=404, detail="Аудитория не найдена")
        return _ok(item)

    @router.patch("/audiences/{audience_id}")
    def patch_audience(audience_id: str, body: AudienceCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = audience_service.update_audience(
            audience_id, actor.username, {"name": body.name}, visible_owners=_visibility(actor)
        )
        if not item:
            raise HTTPException(status_code=404, detail="Аудитория не найдена")
        return _ok(item)

    @router.post("/audiences/{audience_id}/duplicate")
    def post_audience_duplicate(audience_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        item = audience_service.duplicate_audience(audience_id, actor.username, visible_owners=_visibility(actor))
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
                audience_id, actor.username, limit=limit, offset=offset, q=q,
                visible_owners=_visibility(actor),
            )
        )

    @router.put("/audiences/{audience_id}/members")
    def put_audience_members(
        audience_id: str, body: AudienceMembersBody, principal: object = Depends(check_auth)
    ):
        actor = _actor(principal)
        try:
            return _ok(audience_service.replace_members(
                audience_id, actor.username, body.members, visible_owners=_visibility(actor),
            ))
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
            rows, _columns = service.parse_recipients_csv(content)
        elif filename.endswith(".xlsx") or filename.endswith(".xlsm"):
            rows, _columns = service.parse_recipients_xlsx(content)
        else:
            raise HTTPException(status_code=400, detail="Поддерживаются CSV и XLSX")
        try:
            result = audience_service.replace_members(
                audience_id, actor.username, rows, visible_owners=_visibility(actor),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ok({"import": result, "preview": rows[:20]})

    @router.post("/audiences/{audience_id}/use-in-campaign/{campaign_id}")
    def post_use_in_campaign(
        audience_id: str, campaign_id: str, principal: object = Depends(check_auth)
    ):
        actor = _actor(principal)
        try:
            return _ok(audience_service.copy_audience_to_campaign(
                audience_id, campaign_id, actor.username, visible_owners=_visibility(actor),
            ))
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
