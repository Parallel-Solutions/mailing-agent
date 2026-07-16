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
    spa_index_path=None,
) -> APIRouter:
    router = APIRouter()
    ttl_days = max(1, int(getattr(settings_obj, "app_session_ttl_days", 7) or 7))
    use_legacy_ui = bool(getattr(settings_obj, "use_legacy_ui", False))

    def _auth_html(legacy_path) -> HTMLResponse:
        from pathlib import Path

        spa_path = Path(spa_index_path) if spa_index_path else None
        if not use_legacy_ui and spa_path is not None and spa_path.exists():
            return HTMLResponse(
                content=spa_path.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return HTMLResponse(content=legacy_path.read_text(encoding="utf-8"))

    @router.get("/login", response_class=HTMLResponse)
    def login_page():
        return _auth_html(login_template_path)

    @router.get("/register", response_class=HTMLResponse)
    async def register_page():
        if not bool(getattr(settings_obj, "app_allow_registration", False)):
            raise HTTPException(status_code=403, detail="Регистрация отключена. Обратитесь к администратору.")
        return _auth_html(register_template_path)

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
        return {
            "status": "ok",
            "result": {
                "user": principal_payload(principal),
            },
        }

    @router.post("/api/admin/users")
    async def admin_create_user(payload: AuthRegisterRequest, principal: object = Depends(check_auth)):
        from src.jobs.access import coerce_principal

        actor = coerce_principal(principal)
        if not actor.is_admin:
            raise HTTPException(status_code=403, detail="Только администратор может создавать пользователей.")
        try:
            record = create_user(payload.username, payload.password)
        except UserStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "ok",
            "result": {
                "user": principal_payload(principal_from_user_record(record)),
            },
        }

    return router
