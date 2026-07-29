"""Company management API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from src.campaigns import company_service
from src.campaigns.company_service import CompanyServiceError
from src.jobs.access import coerce_principal, principal_payload
from src.security.company_access import (
    TEMPORARY_GLOBAL_ORGANIZATION_ACCESS,
    can_manage_company,
    can_view_company,
    require_app_admin,
    require_company_admin,
    require_company_view,
)


class CompanyCreateBody(BaseModel):
    name: str
    phone: str | None = None
    contact_person_name: str | None = None


class CompanyUpdateBody(BaseModel):
    name: str | None = None
    phone: str | None = None
    contact_person_name: str | None = None


class CompanyMemberCreateBody(BaseModel):
    username: str
    password: str | None = None
    role: str = "member"


class CompanyMemberUpdateBody(BaseModel):
    role: str


class CompanyWorkTypeCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class CompanyWorkTypeUpdateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)


def _ok(result: Any) -> dict[str, Any]:
    return {"status": "ok", "result": result}


def _actor(principal: object) -> Principal:
    return coerce_principal(principal)


def _company_payload(principal: Principal, company: dict[str, Any]) -> dict[str, Any]:
    payload = dict(company)
    user_payload = principal_payload(principal)
    user_payload["company"] = {
        "id": payload["id"],
        "name": payload["name"],
        "logo_url": payload.get("logo_url"),
    }
    return user_payload


def create_companies_router(*, check_auth: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/companies/me")
    def get_my_company(principal: object = Depends(check_auth)):
        actor = _actor(principal)
        company = company_service.get_company_for_user(actor.username)
        if company is None:
            return _ok(None)
        return _ok(company)

    @router.get("/companies")
    def list_companies(
        principal: object = Depends(check_auth),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        # TODO(security): restore app-admin scoping together with
        # TEMPORARY_GLOBAL_ORGANIZATION_ACCESS.
        if not TEMPORARY_GLOBAL_ORGANIZATION_ACCESS:
            require_app_admin(_actor(principal))
        return _ok(company_service.list_companies(limit=limit, offset=offset))

    @router.post("/companies")
    def create_company(body: CompanyCreateBody, principal: object = Depends(check_auth)):
        require_app_admin(_actor(principal))
        try:
            return _ok(
                company_service.create_company(
                    name=body.name,
                    phone=body.phone or "",
                    contact_person_name=body.contact_person_name or "",
                )
            )
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/companies/{company_id}")
    def get_company(company_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_view(actor, company_id)
        company = company_service.get_company(company_id)
        if company is None:
            raise HTTPException(status_code=404, detail="Компания не найдена.")
        return _ok(company)

    @router.patch("/companies/{company_id}")
    def patch_company(company_id: str, body: CompanyUpdateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        try:
            company = company_service.update_company(company_id, body.model_dump(exclude_none=True))
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if company is None:
            raise HTTPException(status_code=404, detail="Компания не найдена.")
        return _ok(company)

    @router.delete("/companies/{company_id}")
    def delete_company(company_id: str, principal: object = Depends(check_auth)):
        require_app_admin(_actor(principal))
        if not company_service.delete_company(company_id):
            raise HTTPException(status_code=404, detail="Компания не найдена.")
        return _ok({"removed": True})

    @router.post("/companies/{company_id}/logo")
    def upload_logo(company_id: str, file: UploadFile = File(...), principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        filename = str(file.filename or "").lower()
        if not any(filename.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
            raise HTTPException(status_code=400, detail="Поддерживаются только PNG, JPEG и WebP.")
        data = file.file.read()
        if len(data) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Логотип не должен превышать 2 МБ.")
        try:
            company = company_service.upload_company_logo(company_id, data, file.content_type or "")
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if company is None:
            raise HTTPException(status_code=404, detail="Компания не найдена.")
        return _ok(company)

    @router.delete("/companies/{company_id}/logo")
    def delete_logo(company_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        company = company_service.delete_company_logo(company_id)
        if company is None:
            raise HTTPException(status_code=404, detail="Компания не найдена.")
        return _ok(company)

    @router.get("/companies/{company_id}/logo")
    def get_logo(company_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        if not can_view_company(actor, company_id):
            raise HTTPException(status_code=403, detail="Нет доступа к логотипу компании.")
        payload = company_service.get_company_logo(company_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Логотип не найден.")
        data, content_type = payload
        return Response(content=data, media_type=content_type)

    @router.get("/companies/{company_id}/members")
    def list_members(company_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_view(actor, company_id)
        if company_service.get_company(company_id) is None:
            raise HTTPException(status_code=404, detail="Компания не найдена.")
        return _ok(company_service.list_members(company_id))

    @router.post("/companies/{company_id}/members")
    def add_member(company_id: str, body: CompanyMemberCreateBody, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        try:
            member = company_service.add_member(
                company_id,
                body.username,
                role=body.role,
                password=body.password,
            )
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _ok(member)

    @router.patch("/companies/{company_id}/members/{username}")
    def patch_member(
        company_id: str,
        username: str,
        body: CompanyMemberUpdateBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        try:
            member = company_service.update_member_role(company_id, username, body.role)
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if member is None:
            raise HTTPException(status_code=404, detail="Участник не найден.")
        return _ok(member)

    @router.delete("/companies/{company_id}/members/{username}")
    def remove_member(company_id: str, username: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        if actor.username == username:
            raise HTTPException(status_code=400, detail="Нельзя удалить себя из компании.")
        if not company_service.remove_member(company_id, username):
            raise HTTPException(status_code=404, detail="Участник не найден.")
        return _ok({"removed": True})

    @router.get("/companies/{company_id}/work-types")
    def list_work_types(company_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_view(actor, company_id)
        try:
            return _ok(company_service.list_company_work_types(company_id))
        except CompanyServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/companies/{company_id}/work-types")
    def create_work_type(
        company_id: str,
        body: CompanyWorkTypeCreateBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        try:
            return _ok(company_service.create_company_work_type(company_id, name=body.name))
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/companies/{company_id}/work-types/{work_type_id}")
    def patch_work_type(
        company_id: str,
        work_type_id: str,
        body: CompanyWorkTypeUpdateBody,
        principal: object = Depends(check_auth),
    ):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        try:
            item = company_service.update_company_work_type(company_id, work_type_id, name=body.name)
        except CompanyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Вид работ не найден.")
        return _ok(item)

    @router.delete("/companies/{company_id}/work-types/{work_type_id}")
    def delete_work_type(company_id: str, work_type_id: str, principal: object = Depends(check_auth)):
        actor = _actor(principal)
        require_company_admin(actor, company_id)
        if not company_service.delete_company_work_type(company_id, work_type_id):
            raise HTTPException(status_code=404, detail="Вид работ не найден.")
        return _ok({"removed": True})

    return router
