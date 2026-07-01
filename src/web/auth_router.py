from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse

from src.jobs.access import principal_payload
from src.security.auth import authenticate_user, principal_from_user_record
from src.security.session_store import SESSION_COOKIE_NAME, create_session, delete_session
from src.security.user_store import UserStoreError, create_user
from src.web.request_models import AuthLoginRequest, AuthRegisterRequest


def _cookie_secure(settings_obj: Any) -> bool:
    return str(getattr(settings_obj, "public_base_url", "") or "").lower().startswith("https://")


def _registration_enabled(settings_obj: Any) -> bool:
    value = getattr(settings_obj, "app_allow_registration", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
    login_template_path,
    register_template_path,
) -> APIRouter:
    router = APIRouter()
    ttl_days = max(1, int(getattr(settings_obj, "app_session_ttl_days", 7) or 7))

    @router.get("/login", response_class=HTMLResponse)
    async def login_page():
        return login_template_path.read_text(encoding="utf-8")

    @router.get("/register", response_class=HTMLResponse)
    async def register_page():
        if not _registration_enabled(settings_obj):
            raise HTTPException(status_code=404, detail="Registration disabled.")
        return register_template_path.read_text(encoding="utf-8")

    @router.post("/api/auth/register")
    async def auth_register(payload: AuthRegisterRequest, response: Response):
        if not _registration_enabled(settings_obj):
            raise HTTPException(status_code=404, detail="Registration disabled.")
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
    async def auth_login(payload: AuthLoginRequest, response: Response):
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
    async def auth_logout(
        response: Response,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    ):
        if session_token:
            delete_session(session_token)
        _clear_session_cookie(response, settings_obj=settings_obj)
        return {"status": "ok"}

    @router.get("/api/auth/me")
    async def auth_me(principal: object = Depends(check_auth)):
        return {
            "status": "ok",
            "result": {
                "user": principal_payload(principal),
            },
        }

    return router
