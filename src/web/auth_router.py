from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.jobs.access import principal_payload
from src.security.auth import authenticate_user, principal_from_user_record
from src.security.company_access import (
    COMPANY_ACCESS_MANAGE,
    company_accesses_for_username,
    replace_company_accesses,
    require_app_admin,
)
from src.security.session_store import SESSION_COOKIE_NAME, create_session, delete_session
from src.security.user_store import (
    UserRecord,
    UserStoreError,
    create_user,
    get_user_record,
    list_user_records,
    update_user_role,
    user_record_to_dict,
)
from src.web.request_models import AuthLoginRequest, AuthRegisterRequest


class CompanyAccessBody(BaseModel):
    company_id: str = Field(min_length=1, max_length=36)
    access_level: Literal["view", "manage"] = "view"


class AdminUserCreateBody(BaseModel):
    username: str
    password: str
    password_confirm: str | None = None
    role: Literal["admin", "company_admin", "user"] = "user"
    company_accesses: list[CompanyAccessBody] = Field(default_factory=list)


class AdminUserUpdateBody(BaseModel):
    role: Literal["admin", "company_admin", "user"] | None = None
    company_accesses: list[CompanyAccessBody] | None = None


def _access_dicts(items: list[CompanyAccessBody]) -> list[dict[str, str]]:
    return [item.model_dump() for item in items]


def _validate_role_accesses(role: str, accesses: list[dict[str, str]]) -> None:
    has_manage = any(item["access_level"] == COMPANY_ACCESS_MANAGE for item in accesses)
    if role == "company_admin" and not has_manage:
        raise HTTPException(
            status_code=400,
            detail="Администратору компаний нужно выдать управление хотя бы одной компанией.",
        )
    if role == "user" and has_manage:
        raise HTTPException(
            status_code=400,
            detail="Обычному пользователю нельзя выдать право настройки компании.",
        )


def _admin_user_payload(record: UserRecord) -> dict[str, Any]:
    payload = user_record_to_dict(record)
    payload["company_accesses"] = company_accesses_for_username(record.username)
    return payload


def _cookie_secure(settings_obj: Any) -> bool:
    return str(getattr(settings_obj, "public_base_url", "") or "").lower().startswith("https://")


def _set_session_cookie(response: Response, token: str, *, settings_obj: Any, ttl_days: int) -> None:
    max_age = max(1, int(ttl_days)) * 24 * 60 * 60
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(settings_obj),
        path="/",
    )


def _clear_session_cookie(response: Response, *, settings_obj: Any) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(settings_obj),
    )


def create_auth_router(
    *,
    settings_obj: Any,
    check_auth: Any,
    spa_index_path=None,
    # Deprecated kwargs kept for older test call sites; ignored.
    login_template_path=None,
    register_template_path=None,
) -> APIRouter:
    router = APIRouter()
    ttl_days = max(1, int(getattr(settings_obj, "app_session_ttl_days", 7) or 7))

    def _spa_auth_html() -> HTMLResponse:
        from pathlib import Path

        spa_path = Path(spa_index_path) if spa_index_path else None
        if spa_path is not None and spa_path.exists():
            return HTMLResponse(
                content=spa_path.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        raise HTTPException(status_code=503, detail="Frontend SPA is not built.")

    @router.get("/login", response_class=HTMLResponse)
    def login_page():
        return _spa_auth_html()

    @router.get("/register", response_class=HTMLResponse)
    async def register_page():
        if not bool(getattr(settings_obj, "app_allow_registration", False)):
            raise HTTPException(status_code=403, detail="Регистрация отключена. Обратитесь к администратору.")
        return _spa_auth_html()

    @router.post("/api/auth/register")
    async def auth_register(payload: AuthRegisterRequest, response: Response):
        if not bool(getattr(settings_obj, "app_allow_registration", False)):
            raise HTTPException(status_code=403, detail="Регистрация отключена. Обратитесь к администратору.")
        if payload.password_confirm is not None and payload.password != payload.password_confirm:
            raise HTTPException(status_code=400, detail="Пароли не совпадают.")
        try:
            record = create_user(payload.username, payload.password)
        except UserStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        token = create_session(record.username, ttl_days=ttl_days)
        _set_session_cookie(response, token, settings_obj=settings_obj, ttl_days=ttl_days)
        return {
            "status": "ok",
            "result": {
                "user": principal_payload(principal_from_user_record(record)),
            },
        }

    @router.post("/api/auth/login")
    def auth_login(payload: AuthLoginRequest, response: Response):
        principal = authenticate_user(payload.username, payload.password)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный логин или пароль",
            )
        token = create_session(principal.username, ttl_days=ttl_days)
        _set_session_cookie(response, token, settings_obj=settings_obj, ttl_days=ttl_days)
        return {
            "status": "ok",
            "result": {
                "user": principal_payload(principal),
            },
        }

    @router.post("/api/auth/logout")
    def auth_logout(
        response: Response,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    ):
        if session_token:
            delete_session(session_token)
        _clear_session_cookie(response, settings_obj=settings_obj)
        return {"status": "ok"}

    @router.get("/api/auth/me")
    def auth_me(principal: object = Depends(check_auth)):
        from src.campaigns import company_service
        from src.jobs.access import coerce_principal

        actor = coerce_principal(principal)
        user_payload = principal_payload(principal)
        if actor.company_id:
            company = company_service.get_company(actor.company_id)
            if company:
                user_payload["company"] = {
                    "id": company["id"],
                    "name": company["name"],
                    "logo_url": company.get("logo_url"),
                }
        return {
            "status": "ok",
            "result": {
                "user": user_payload,
            },
        }

    @router.get("/api/admin/users")
    def admin_list_users(principal: object = Depends(check_auth)):
        require_app_admin(principal)
        items = [_admin_user_payload(record) for record in list_user_records()]
        return {"status": "ok", "result": {"items": items, "total": len(items)}}

    @router.post("/api/admin/users")
    def admin_create_user(
        payload: AdminUserCreateBody,
        principal: object = Depends(check_auth),
    ):
        from src.campaigns import company_service

        actor = require_app_admin(principal)
        if (
            payload.password_confirm is not None
            and payload.password != payload.password_confirm
        ):
            raise HTTPException(status_code=400, detail="Пароли не совпадают.")
        accesses = _access_dicts(payload.company_accesses)
        _validate_role_accesses(payload.role, accesses)
        for access in accesses:
            if company_service.get_company(access["company_id"]) is None:
                raise HTTPException(status_code=400, detail="Компания не найдена.")
        try:
            record = create_user(
                payload.username,
                payload.password,
                role=payload.role,
            )
            replace_company_accesses(
                record.username,
                accesses,
                created_by=actor.username,
            )
        except (UserStoreError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "ok",
            "result": {"user": _admin_user_payload(record)},
        }

    @router.patch("/api/admin/users/{username}")
    def admin_update_user(
        username: str,
        payload: AdminUserUpdateBody,
        principal: object = Depends(check_auth),
    ):
        from src.campaigns import company_service

        actor = require_app_admin(principal)
        record = get_user_record(username)
        if record is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        role = payload.role or record.role
        accesses = (
            _access_dicts(payload.company_accesses)
            if payload.company_accesses is not None
            else company_accesses_for_username(username)
        )
        _validate_role_accesses(role, accesses)
        for access in accesses:
            if company_service.get_company(str(access["company_id"])) is None:
                raise HTTPException(status_code=400, detail="Компания не найдена.")
        try:
            if payload.role is not None:
                record = update_user_role(username, payload.role)
            if payload.company_accesses is not None:
                replace_company_accesses(
                    username,
                    accesses,
                    created_by=actor.username,
                )
        except (UserStoreError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        refreshed = get_user_record(username) or record
        return {
            "status": "ok",
            "result": {"user": _admin_user_payload(refreshed)},
        }

    return router
