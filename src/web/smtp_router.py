from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.generator.delivery.smtp_mailboxes import (
    create_mailbox,
    delete_mailbox,
    humanize_smtp_error,
    list_mailboxes,
    resolve_smtp_credentials,
    send_test_email,
    set_default_mailbox,
    update_mailbox,
    verify_smtp_credentials,
)
from src.generator.delivery.smtp_providers import list_provider_presets, resolve_provider_settings
from src.jobs.audit import append_audit_event
from src.security.auth import Principal, coerce_principal
from src.security.credential_vault import CredentialVaultError
from src.web.request_models import _clean_optional_text, _clean_required_text
from src.web.responses import ok_response


class SmtpMailboxCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "custom"
    email: str
    password: str
    sender_name: str = ""
    host: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    make_default: bool = False
    send_test: bool = True

    @field_validator("provider", "email", "password", "sender_name", "host", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class SmtpMailboxUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    email: str | None = None
    password: str | None = None
    sender_name: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    send_test: bool = False


class SmtpMailboxTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "custom"
    email: str
    password: str = ""
    sender_name: str = ""
    host: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    mailbox_id: str | None = None
    send_test_email_to: str | None = None


def _actor_username(principal: object) -> str:
    return coerce_principal(principal).username


def _can_access_mailbox(actor: Principal, owner_username: str) -> bool:
    return actor.is_admin or actor.username == owner_username


def create_smtp_router(*, check_auth: Callable[..., object]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/smtp/providers")
    def smtp_providers(principal: object = Depends(check_auth)):
        del principal
        return ok_response({"providers": list_provider_presets()})

    @router.get("/api/smtp/mailboxes")
    def smtp_mailboxes_list(principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        return ok_response({"mailboxes": list_mailboxes(actor.username)})

    @router.post("/api/smtp/mailboxes")
    def smtp_mailboxes_create(payload: SmtpMailboxCreateRequest, principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        try:
            preset = resolve_provider_settings(
                payload.provider,
                host=payload.host,
                port=payload.port,
                use_ssl=payload.use_ssl,
                use_starttls=payload.use_starttls,
            )
            if payload.send_test:
                from src.generator.delivery.smtp_mailboxes import ResolvedSmtpCredentials

                test_credentials = ResolvedSmtpCredentials(
                    email=_clean_required_text(payload.email).lower(),
                    password=_clean_required_text(payload.password),
                    host=preset.host,
                    port=preset.port,
                    use_ssl=preset.use_ssl,
                    use_starttls=preset.use_starttls,
                    sender_name=_clean_optional_text(payload.sender_name) or "",
                )
                send_test_email(test_credentials)
            mailbox = create_mailbox(
                owner_username=actor.username,
                provider=payload.provider,
                email=payload.email,
                password=payload.password,
                sender_name=payload.sender_name,
                host=payload.host,
                port=payload.port,
                use_ssl=payload.use_ssl,
                use_starttls=payload.use_starttls,
                make_default=payload.make_default,
            )
        except CredentialVaultError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=humanize_smtp_error(exc)) from exc
        append_audit_event(action="smtp.mailbox.create", principal=principal, details={"mailbox_id": mailbox["id"]})
        return ok_response({"mailbox": mailbox})

    @router.patch("/api/smtp/mailboxes/{mailbox_id}")
    def smtp_mailboxes_update(
        mailbox_id: str,
        payload: SmtpMailboxUpdateRequest,
        principal: object = Depends(check_auth),
    ):
        actor = coerce_principal(principal)
        try:
            mailbox = update_mailbox(
                mailbox_id,
                owner_username=actor.username,
                provider=payload.provider,
                email=payload.email,
                password=payload.password or None,
                sender_name=payload.sender_name,
                host=payload.host,
                port=payload.port,
                use_ssl=payload.use_ssl,
                use_starttls=payload.use_starttls,
            )
            if payload.send_test:
                credentials = resolve_smtp_credentials(mailbox_id=mailbox_id, owner_username=actor.username)
                send_test_email(credentials)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CredentialVaultError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=humanize_smtp_error(exc)) from exc
        append_audit_event(action="smtp.mailbox.update", principal=principal, details={"mailbox_id": mailbox_id})
        return ok_response({"mailbox": mailbox})

    @router.delete("/api/smtp/mailboxes/{mailbox_id}")
    def smtp_mailboxes_delete(mailbox_id: str, principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        try:
            delete_mailbox(mailbox_id, owner_username=actor.username)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        append_audit_event(action="smtp.mailbox.delete", principal=principal, details={"mailbox_id": mailbox_id})
        return ok_response({"deleted": True})

    @router.post("/api/smtp/mailboxes/{mailbox_id}/default")
    def smtp_mailboxes_default(mailbox_id: str, principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        try:
            mailbox = set_default_mailbox(mailbox_id, owner_username=actor.username)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        append_audit_event(action="smtp.mailbox.default", principal=principal, details={"mailbox_id": mailbox_id})
        return ok_response({"mailbox": mailbox})

    @router.post("/api/smtp/mailboxes/{mailbox_id}/test")
    def smtp_mailboxes_test_saved(mailbox_id: str, principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        try:
            credentials = resolve_smtp_credentials(mailbox_id=mailbox_id, owner_username=actor.username)
            send_test_email(credentials)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=humanize_smtp_error(exc)) from exc
        return ok_response({"status": "ok", "message": "SMTP-подключение проверено, тестовое письмо отправлено."})

    @router.post("/api/smtp/test")
    def smtp_test_connection(payload: SmtpMailboxTestRequest, principal: object = Depends(check_auth)):
        del principal
        try:
            if payload.mailbox_id:
                actor = coerce_principal(principal)
                credentials = resolve_smtp_credentials(mailbox_id=payload.mailbox_id, owner_username=actor.username)
            else:
                preset = resolve_provider_settings(
                    payload.provider,
                    host=payload.host,
                    port=payload.port,
                    use_ssl=payload.use_ssl,
                    use_starttls=payload.use_starttls,
                )
                from src.generator.delivery.smtp_mailboxes import ResolvedSmtpCredentials

                credentials = ResolvedSmtpCredentials(
                    email=_clean_required_text(payload.email).lower(),
                    password=_clean_required_text(payload.password),
                    host=preset.host,
                    port=preset.port,
                    use_ssl=preset.use_ssl,
                    use_starttls=preset.use_starttls,
                    sender_name=_clean_optional_text(payload.sender_name) or "",
                )
            if payload.send_test_email_to:
                send_test_email(credentials, recipient=payload.send_test_email_to)
            else:
                verify_smtp_credentials(credentials)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=humanize_smtp_error(exc)) from exc
        return ok_response({"status": "ok", "message": "SMTP-подключение успешно проверено."})

    return router
