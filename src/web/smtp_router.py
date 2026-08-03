from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.generator.delivery.smtp_mailboxes import (
    ResolvedSmtpCredentials,
    build_oauth_credentials,
    create_mailbox,
    delete_mailbox,
    humanize_smtp_error,
    list_mailboxes,
    mark_mailbox_status,
    resolve_smtp_credentials,
    send_test_email,
    set_default_mailbox,
    update_mailbox,
    verify_and_mark_mailbox,
    verify_smtp_credentials,
)
from src.generator.delivery.smtp_autodiscover import discover_smtp_settings, parse_discover_email, result_to_dict
from src.generator.delivery.smtp_oauth import (
    OAuthTokens,
    build_oauth_authorize_url,
    exchange_oauth_code,
    is_oauth_provider_configured,
)
from src.generator.delivery.smtp_providers import list_provider_presets, resolve_provider_settings
from src.generator.delivery.smtp_setup_orchestrator import (
    analyze_smtp_setup,
    create_oauth_state,
    pop_oauth_state,
    verify_smtp_setup,
)
from src.jobs.audit import append_audit_event
from src.security.auth import Principal, coerce_principal
from src.security.credential_vault import CredentialVaultError
from src.web.request_models import _clean_optional_text, _clean_required_text
from src.web.responses import ok_response


class SmtpMailboxCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "custom"
    email: str
    password: str = ""
    sender_name: str = ""
    host: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    make_default: bool = False
    send_test: bool = True
    auth_method: str = "password"
    oauth_provider: str | None = None
    oauth_tokens: dict[str, Any] | None = None
    smtp_username: str | None = None
    save_sent_copy: bool = True
    imap_host: str = ""
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool | None = None
    imap_use_starttls: bool | None = None
    imap_username: str | None = None
    imap_password: str = ""
    imap_sent_folder: str = ""
    setup_session_id: str | None = None

    @field_validator(
        "provider",
        "email",
        "password",
        "sender_name",
        "host",
        "auth_method",
        "oauth_provider",
        "smtp_username",
        "imap_host",
        "imap_username",
        "imap_password",
        "imap_sent_folder",
        mode="before",
    )
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
    auth_method: str | None = None
    oauth_provider: str | None = None
    oauth_tokens: dict[str, Any] | None = None
    smtp_username: str | None = None
    save_sent_copy: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool | None = None
    imap_use_starttls: bool | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    imap_sent_folder: str | None = None


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
    include_sample_attachment: bool = False
    auth_method: str = "password"
    oauth_provider: str | None = None
    oauth_tokens: dict[str, Any] | None = None
    smtp_username: str | None = None


class SmtpSetupAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    last_error: str = ""
    attempt_count: int = 0
    setup_session_id: str | None = None


class SmtpSetupVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup_session_id: str
    email: str
    password: str = ""
    provider: str = "custom"
    host: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    use_starttls: bool | None = None
    auth_method: str = "password"
    oauth_provider: str | None = None
    oauth_tokens: dict[str, Any] | None = None
    smtp_username: str | None = None


class SmtpOAuthExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    code: str
    state: str


def _actor_username(principal: object) -> str:
    return coerce_principal(principal).username


def _can_access_mailbox(actor: Principal, owner_username: str) -> bool:
    from src.security.company_access import can_view_owned_resource

    return can_view_owned_resource(actor, owner_username)


def _parse_oauth_tokens(payload: dict[str, Any] | None) -> OAuthTokens | None:
    if not payload:
        return None
    return OAuthTokens.from_dict(payload)


def _build_test_credentials(payload: SmtpMailboxTestRequest) -> ResolvedSmtpCredentials:
    preset = resolve_provider_settings(
        payload.provider,
        host=payload.host,
        port=payload.port,
        use_ssl=payload.use_ssl,
        use_starttls=payload.use_starttls,
    )
    email = _clean_required_text(payload.email).lower()
    oauth_tokens = _parse_oauth_tokens(payload.oauth_tokens)
    if payload.auth_method == "oauth" and oauth_tokens and payload.oauth_provider:
        return build_oauth_credentials(
            email=email,
            provider=_clean_required_text(payload.oauth_provider),
            tokens=oauth_tokens,
            host=preset.host,
            port=preset.port,
            use_ssl=preset.use_ssl,
            use_starttls=preset.use_starttls,
            sender_name=_clean_optional_text(payload.sender_name) or "",
            smtp_username=payload.smtp_username,
        )
    return ResolvedSmtpCredentials(
        email=email,
        password=_clean_required_text(payload.password),
        host=preset.host,
        port=preset.port,
        use_ssl=preset.use_ssl,
        use_starttls=preset.use_starttls,
        sender_name=_clean_optional_text(payload.sender_name) or "",
        smtp_username=_clean_optional_text(payload.smtp_username) or email,
    )


def create_smtp_router(*, check_auth: Callable[..., object]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/smtp/providers")
    def smtp_providers(principal: object = Depends(check_auth)):
        del principal
        return ok_response({"providers": list_provider_presets()})

    @router.get("/api/smtp/discover")
    def smtp_discover(email: str = "", principal: object = Depends(check_auth)):
        del principal
        normalized_email = str(email or "").strip()
        if not normalized_email:
            raise HTTPException(status_code=400, detail="Укажите корректный email.")
        try:
            normalized_email, domain = parse_discover_email(normalized_email)
            discovered = discover_smtp_settings(normalized_email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if discovered is None:
            return ok_response(
                {
                    "email": normalized_email,
                    "domain": domain,
                    "discovered": False,
                }
            )
        return ok_response(result_to_dict(discovered, email=normalized_email, domain=domain))

    @router.post("/api/smtp/setup/analyze")
    def smtp_setup_analyze(payload: SmtpSetupAnalyzeRequest, principal: object = Depends(check_auth)):
        del principal
        try:
            analysis = analyze_smtp_setup(
                payload.email,
                last_error=payload.last_error,
                attempt_count=payload.attempt_count,
                setup_session_id=payload.setup_session_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Не удалось выполнить анализ SMTP. Попробуйте позже или укажите сервер вручную.",
            ) from exc
        return ok_response(analysis.to_dict())

    @router.post("/api/smtp/setup/verify")
    def smtp_setup_verify(payload: SmtpSetupVerifyRequest, principal: object = Depends(check_auth)):
        del principal
        try:
            oauth_tokens = _parse_oauth_tokens(payload.oauth_tokens)
            result = verify_smtp_setup(
                setup_session_id=payload.setup_session_id,
                email=payload.email,
                password=payload.password,
                oauth_provider=payload.oauth_provider,
                oauth_tokens=oauth_tokens,
                provider=payload.provider,
                host=payload.host,
                port=payload.port,
                use_ssl=payload.use_ssl,
                use_starttls=payload.use_starttls,
                smtp_username=payload.smtp_username,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ok_response(result)

    @router.get("/api/smtp/oauth/start")
    def smtp_oauth_start(
        provider: str = Query(...),
        email: str = Query(...),
        setup_session_id: str = Query(...),
        principal: object = Depends(check_auth),
    ):
        del principal
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider not in {"google", "microsoft"}:
            raise HTTPException(status_code=400, detail="Неподдерживаемый OAuth-провайдер.")
        if not is_oauth_provider_configured(normalized_provider):
            raise HTTPException(status_code=503, detail="OAuth-провайдер не настроен на сервере.")
        try:
            normalized_email, _domain = parse_discover_email(email)
            state = create_oauth_state(
                setup_session_id=setup_session_id,
                provider=normalized_provider,
                email=normalized_email,
            )
            authorize_url = build_oauth_authorize_url(
                provider=normalized_provider,
                state=state,
                email=normalized_email,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ok_response({"authorize_url": authorize_url, "state": state})

    @router.get("/api/smtp/oauth/callback/{provider}")
    def smtp_oauth_callback(provider: str, code: str = "", state: str = "", error: str = ""):
        normalized_provider = str(provider or "").strip().lower()
        if error:
            return HTMLResponse(_oauth_popup_html(success=False, message=error), status_code=400)
        oauth_state = pop_oauth_state(state)
        if oauth_state is None:
            return HTMLResponse(_oauth_popup_html(success=False, message="Сессия OAuth истекла."), status_code=400)
        try:
            tokens = exchange_oauth_code(provider=normalized_provider, code=code)
        except Exception as exc:
            return HTMLResponse(_oauth_popup_html(success=False, message=str(exc)), status_code=400)
        payload = {
            "provider": normalized_provider,
            "email": oauth_state.get("email"),
            "setup_session_id": oauth_state.get("setup_session_id"),
            "tokens": tokens.to_dict(),
        }
        return HTMLResponse(_oauth_popup_html(success=True, payload=payload))

    @router.post("/api/smtp/oauth/exchange")
    def smtp_oauth_exchange(payload: SmtpOAuthExchangeRequest, principal: object = Depends(check_auth)):
        del principal
        oauth_state = pop_oauth_state(payload.state)
        if oauth_state is None:
            raise HTTPException(status_code=400, detail="Сессия OAuth истекла.")
        try:
            tokens = exchange_oauth_code(provider=payload.provider, code=payload.code)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ok_response(
            {
                "provider": payload.provider,
                "email": oauth_state.get("email"),
                "setup_session_id": oauth_state.get("setup_session_id"),
                "tokens": tokens.to_dict(),
            }
        )

    @router.get("/api/smtp/mailboxes")
    def smtp_mailboxes_list(principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        return ok_response({"mailboxes": list_mailboxes(actor.username)})

    @router.post("/api/smtp/mailboxes")
    def smtp_mailboxes_create(payload: SmtpMailboxCreateRequest, principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        oauth_tokens = _parse_oauth_tokens(payload.oauth_tokens)
        try:
            preset = resolve_provider_settings(
                payload.provider,
                host=payload.host,
                port=payload.port,
                use_ssl=payload.use_ssl,
                use_starttls=payload.use_starttls,
            )
            auth_method = _clean_optional_text(payload.auth_method) or "password"
            test_credentials: ResolvedSmtpCredentials
            if auth_method == "oauth" and oauth_tokens and payload.oauth_provider:
                test_credentials = build_oauth_credentials(
                    email=_clean_required_text(payload.email).lower(),
                    provider=_clean_required_text(payload.oauth_provider),
                    tokens=oauth_tokens,
                    host=preset.host,
                    port=preset.port,
                    use_ssl=preset.use_ssl,
                    use_starttls=preset.use_starttls,
                    sender_name=_clean_optional_text(payload.sender_name) or "",
                    smtp_username=payload.smtp_username,
                )
            else:
                test_credentials = ResolvedSmtpCredentials(
                    email=_clean_required_text(payload.email).lower(),
                    password=_clean_required_text(payload.password),
                    host=preset.host,
                    port=preset.port,
                    use_ssl=preset.use_ssl,
                    use_starttls=preset.use_starttls,
                    sender_name=_clean_optional_text(payload.sender_name) or "",
                    smtp_username=_clean_optional_text(payload.smtp_username) or _clean_required_text(payload.email).lower(),
                )
            if payload.send_test:
                verify_and_mark_mailbox(test_credentials, send_test=True)
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
                auth_method=auth_method,
                oauth_provider=payload.oauth_provider,
                oauth_tokens=oauth_tokens,
                smtp_username=payload.smtp_username,
                save_sent_copy=payload.save_sent_copy,
                imap_host=payload.imap_host,
                imap_port=payload.imap_port,
                imap_use_ssl=payload.imap_use_ssl,
                imap_use_starttls=payload.imap_use_starttls,
                imap_username=payload.imap_username,
                imap_password=payload.imap_password,
                imap_sent_folder=payload.imap_sent_folder,
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
        oauth_tokens = _parse_oauth_tokens(payload.oauth_tokens)
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
                auth_method=payload.auth_method,
                oauth_provider=payload.oauth_provider,
                oauth_tokens=oauth_tokens,
                smtp_username=payload.smtp_username,
                save_sent_copy=payload.save_sent_copy,
                imap_host=payload.imap_host,
                imap_port=payload.imap_port,
                imap_use_ssl=payload.imap_use_ssl,
                imap_use_starttls=payload.imap_use_starttls,
                imap_username=payload.imap_username,
                imap_password=payload.imap_password,
                imap_sent_folder=payload.imap_sent_folder,
            )
            if payload.send_test:
                credentials = resolve_smtp_credentials(mailbox_id=mailbox_id, owner_username=actor.username)
                verify_and_mark_mailbox(credentials, mailbox_id=mailbox_id, send_test=True)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CredentialVaultError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            mark_mailbox_status(mailbox_id, status="auth_failed", last_error=humanize_smtp_error(exc))
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
            verify_and_mark_mailbox(credentials, mailbox_id=mailbox_id, send_test=True)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=humanize_smtp_error(exc)) from exc
        return ok_response({"status": "ok", "message": "SMTP-подключение проверено, тестовое письмо отправлено."})

    @router.post("/api/smtp/mailboxes/{mailbox_id}/test-imap")
    def smtp_mailboxes_test_imap(mailbox_id: str, principal: object = Depends(check_auth)):
        actor = coerce_principal(principal)
        try:
            from src.generator.delivery.imap_sent import verify_imap_connection

            result = verify_imap_connection(
                mailbox_id=mailbox_id,
                owner_username=actor.username,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ok_response({"status": result["status"], "folder": result.get("folder", "")})

    @router.post("/api/smtp/test")
    def smtp_test_connection(payload: SmtpMailboxTestRequest, principal: object = Depends(check_auth)):
        try:
            if payload.mailbox_id:
                actor = coerce_principal(principal)
                credentials = resolve_smtp_credentials(mailbox_id=payload.mailbox_id, owner_username=actor.username)
            else:
                credentials = _build_test_credentials(payload)
            if payload.send_test_email_to:
                verify_and_mark_mailbox(
                    credentials,
                    mailbox_id=payload.mailbox_id,
                    send_test=True,
                    recipient=payload.send_test_email_to,
                    include_sample_attachment=bool(payload.include_sample_attachment),
                )
            else:
                verify_and_mark_mailbox(credentials, mailbox_id=payload.mailbox_id, send_test=False)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=humanize_smtp_error(exc)) from exc
        return ok_response({"status": "ok", "message": "SMTP-подключение успешно проверено."})

    return router


def _oauth_popup_html(*, success: bool, message: str = "", payload: dict[str, Any] | None = None) -> str:
    import json

    data = {
        "success": success,
        "message": message,
        "payload": payload or {},
    }
    serialized = json.dumps(data, ensure_ascii=False)
    tone = "success" if success else "error"
    title = "SMTP OAuth" if success else "Ошибка OAuth"
    return f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<script>
  const payload = {serialized};
  if (window.opener) {{
    window.opener.postMessage({{ type: "smtp-oauth-{tone}", ...payload }}, window.location.origin);
  }}
  window.close();
</script>
<p>{message or ("OAuth завершён." if success else "OAuth не удался.")}</p>
</body>
</html>"""
