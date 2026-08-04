from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.campaigns.recipient_resend_service import (
    RecipientResendNotAllowed,
    enqueue_recipient_resend,
)

from src.generator.delivery.chain_consent_stats import (
    ChainConsentStatsContext,
    build_chain_subscribes_view,
    build_unsubscribes_view,
)
from src.generator.delivery.manager_actions import ACTION_TYPES, append_manager_action
from src.generator.delivery.manager_stats import (
    StatsFilters,
    build_campaign_analytics,
    build_campaign_attempts,
    build_campaign_full_analytics,
    build_campaigns,
    build_consents_view,
    build_domain_delivery_stats,
    build_email_problems,
    build_manager_dashboard,
    build_recipient_detail,
    build_recipients,
    build_reports_view,
    export_report,
    find_report_file,
    parse_row_key,
    normalize_statistics_period,
)
from src.jobs.access import JobAccessDenied, authorize_job_access, job_is_visible
from src.jobs.job_docs import list_job_ids_with_sent_mail
from src.jobs.storage import normalize_job_id
from src.security.auth import coerce_principal
from src.web.download_sources import DOWNLOAD_HEADERS
from src.web.errors import internal_server_error


class ManagerActionRequest(BaseModel):
    action_type: str
    responsible_manager: str = ""
    due_at: str = ""
    comment: str = ""
    priority: bool = False


class ReportExportRequest(BaseModel):
    report_type: str = "delivery_summary"
    period_from: str = ""
    period_to: str = ""
    providers: list[str] = Field(default_factory=list)
    job_id: str | None = None
    fmt: str = "xlsx"
    options: dict[str, Any] = Field(default_factory=dict)


def create_statistics_router(
    *,
    check_auth: Callable[..., Any],
    jobs_dir: Path,
    resolve_job_paths: Callable[[str | None], Any],
    logger: Any,
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> str | None:
        try:
            return authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def _principal_name(principal: object) -> str:
        return coerce_principal(principal).username

    def _validate_statistics_period(period_from: str, period_to: str) -> tuple[str, str]:
        try:
            return normalize_statistics_period(period_from, period_to)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _list_mailing_jobs_for_stats(principal: object) -> list[str]:
        # Statistics only make sense for jobs that actually sent mail. Resolve the
        # candidate list from the database (a single grouped query, already ordered
        # by most recent activity) instead of walking the jobs directory on disk.
        # Admins see the full set; regular users only jobs they own (job_is_visible).
        actor = coerce_principal(principal)
        job_ids = list_job_ids_with_sent_mail()
        if actor.is_admin:
            return job_ids
        return [job_id for job_id in job_ids if job_is_visible(job_id, principal)]

    def _resolve_job_ids(
        principal: object,
        *,
        job_id: str | None = None,
        campaign: str | None = None,
    ) -> tuple[str, ...]:
        selected = normalize_job_id(job_id or campaign)
        if selected:
            ensure_job_access(selected, principal, allow_missing=False)
            return (selected,)
        return tuple(_list_mailing_jobs_for_stats(principal))

    def _build_filters(
        principal: object,
        *,
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        provider: str | None = None,
        providers: str | None = None,
        status: str | None = None,
        recipient_role: str | None = None,
        consent_status: str = "",
        manager_action: str = "",
        organization: str = "",
        problems_only: bool = False,
        q: str = "",
        quick_filter: str = "",
    ) -> StatsFilters:
        provider_values: list[str] = []
        if provider:
            provider_values.append(provider)
        if providers:
            provider_values.extend(item.strip() for item in providers.split(",") if item.strip())
        status_values = [item.strip() for item in (status or "").split(",") if item.strip()]
        role_values = [item.strip() for item in (recipient_role or "").split(",") if item.strip()]
        period_from, period_to = _validate_statistics_period(period_from, period_to)
        return StatsFilters(
            job_ids=_resolve_job_ids(principal, job_id=job_id, campaign=campaign),
            period_from=period_from,
            period_to=period_to,
            providers=tuple(provider_values),
            manager_statuses=tuple(status_values),
            recipient_roles=tuple(role_values),
            consent_status=consent_status,
            manager_action=manager_action,
            organization=organization,
            problems_only=problems_only,
            q=q,
            quick_filter=quick_filter,
        )

    def _build_chain_consent_context(
        principal: object,
        *,
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        q: str = "",
    ) -> ChainConsentStatsContext:
        actor = coerce_principal(principal)
        return ChainConsentStatsContext(
            filters=_build_filters(
                principal,
                job_id=job_id,
                campaign=campaign,
                period_from=period_from,
                period_to=period_to,
                q=q,
            ),
            owner_username=actor.username,
            is_admin=actor.is_admin,
        )

    @router.get("/api/sender/campaigns")
    def sender_campaigns(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        provider: str | None = None,
        status: str | None = None,
        q: str = "",
        principal: object = Depends(check_auth),
    ):
        filters = _build_filters(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            provider=provider,
            status=status,
            q=q,
        )
        return {"status": "ok", "result": build_campaigns(filters)}

    @router.get("/api/sender/recipients")
    def sender_recipients(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        provider: str | None = None,
        status: str | None = None,
        recipient_role: str | None = None,
        manager_action: str = "",
        organization: str = "",
        problems_only: bool = False,
        q: str = "",
        quick_filter: str = "",
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=10, ge=1, le=100),
        principal: object = Depends(check_auth),
    ):
        filters = _build_filters(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            provider=provider,
            status=status,
            recipient_role=recipient_role,
            manager_action=manager_action,
            organization=organization,
            problems_only=problems_only,
            q=q,
            quick_filter=quick_filter,
        )
        return {"status": "ok", "result": build_recipients(filters, page=page, per_page=per_page)}

    @router.get("/api/sender/recipients/{row_key}")
    def sender_recipient_detail(row_key: str, principal: object = Depends(check_auth)):
        try:
            job_id, _, _ = parse_row_key(row_key)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Некорректный идентификатор получателя.") from exc
        ensure_job_access(job_id, principal, allow_missing=False)
        detail = build_recipient_detail(row_key)
        if detail is None:
            raise HTTPException(status_code=404, detail="Получатель не найден.")
        return {"status": "ok", "result": detail}

    @router.post("/api/sender/recipients/{row_key}/action")
    def sender_recipient_action(
        row_key: str,
        payload: ManagerActionRequest = Body(...),
        principal: object = Depends(check_auth),
    ):
        try:
            job_id, row_id, email = parse_row_key(row_key)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Некорректный идентификатор получателя.") from exc
        ensure_job_access(job_id, principal, allow_missing=False)
        detail = build_recipient_detail(row_key)
        if detail is None:
            raise HTTPException(status_code=404, detail="Получатель не найден.")
        action_type = payload.action_type.strip().lower()
        if action_type not in ACTION_TYPES:
            raise HTTPException(status_code=400, detail="Неподдерживаемый тип действия.")
        try:
            record = append_manager_action(
                job_id,
                row_id=row_id,
                recipient_email=email,
                organization=detail.get("organization", ""),
                recipient_name=detail.get("recipient_name", ""),
                action_type=action_type,
                responsible_manager=payload.responsible_manager or _principal_name(principal),
                due_at=payload.due_at,
                comment=payload.comment,
                priority=payload.priority,
                created_by=_principal_name(principal),
            )
        except Exception as exc:
            # Avoid logging row_key directly (it encodes the recipient email).
            try:
                log_job_id, log_row_id, _ = parse_row_key(row_key)
            except ValueError:
                log_job_id, log_row_id = "", ""
            logger.exception("manager_action_save_failed", job_id=log_job_id, row_id=log_row_id)
            raise internal_server_error("Не удалось сохранить действие менеджера.") from exc
        updated = build_recipient_detail(row_key)
        return {"status": "ok", "result": {"action": record, "recipient": updated}}

    @router.post("/api/sender/recipients/{row_key}/resend", status_code=202)
    def sender_recipient_resend(
        row_key: str,
        principal: object = Depends(check_auth),
    ):
        try:
            job_id, row_id, _email = parse_row_key(row_key)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Некорректный идентификатор получателя.") from exc
        ensure_job_access(job_id, principal, allow_missing=False)
        detail = build_recipient_detail(row_key)
        if detail is None:
            raise HTTPException(status_code=404, detail="Получатель не найден.")
        manager_status = str((detail.get("manager_status") or {}).get("key") or "")
        failed_email = str(detail.get("email") or "")
        for item in list(detail.get("emails") or []):
            if str((item.get("manager_status") or {}).get("key") or "") == manager_status:
                failed_email = str(item.get("email") or failed_email)
                break
        try:
            result = enqueue_recipient_resend(
                job_id=job_id,
                row_id=row_id,
                manager_status=manager_status,
                failed_email=failed_email,
                last_event_at=str(detail.get("last_event_at") or ""),
                requested_by=_principal_name(principal),
            )
        except RecipientResendNotAllowed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "ok", "result": result}

    @router.get("/api/sender/consents")
    def sender_consents(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        consent_status: str = "",
        q: str = "",
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=10, ge=1, le=100),
        principal: object = Depends(check_auth),
    ):
        filters = _build_filters(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            consent_status=consent_status,
            q=q,
        )
        return {"status": "ok", "result": build_consents_view(filters, page=page, per_page=per_page)}

    @router.get("/api/sender/chain-subscribes")
    def sender_chain_subscribes(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        q: str = "",
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=10, ge=1, le=100),
        principal: object = Depends(check_auth),
    ):
        ctx = _build_chain_consent_context(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            q=q,
        )
        return {"status": "ok", "result": build_chain_subscribes_view(ctx, page=page, per_page=per_page)}

    @router.get("/api/sender/unsubscribes")
    def sender_unsubscribes(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        q: str = "",
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=10, ge=1, le=100),
        principal: object = Depends(check_auth),
    ):
        ctx = _build_chain_consent_context(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            q=q,
        )
        return {"status": "ok", "result": build_unsubscribes_view(ctx, page=page, per_page=per_page)}

    @router.get("/api/sender/email-problems")
    def sender_email_problems(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        provider: str | None = None,
        q: str = "",
        quick_filter: str = "",
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=10, ge=1, le=100),
        principal: object = Depends(check_auth),
    ):
        filters = _build_filters(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            provider=provider,
            q=q,
            quick_filter=quick_filter,
            problems_only=True,
        )
        return {"status": "ok", "result": build_email_problems(filters, page=page, per_page=per_page)}

    @router.get("/api/sender/campaign-analytics/{job_id}")
    def sender_campaign_analytics(
        job_id: str,
        refresh: bool = False,
        period_from: str = "",
        period_to: str = "",
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=False)
        period_from, period_to = _validate_statistics_period(period_from, period_to)
        return {
            "status": "ok",
            "result": build_campaign_analytics(
                job_id,
                refresh=refresh,
                period_from=period_from,
                period_to=period_to,
            ),
        }

    @router.get("/api/sender/campaign-attempts/{job_id}")
    def sender_campaign_attempts(
        job_id: str,
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=100, ge=1, le=200),
        period_from: str = "",
        period_to: str = "",
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=False)
        period_from, period_to = _validate_statistics_period(period_from, period_to)
        return {
            "status": "ok",
            "result": build_campaign_attempts(
                job_id,
                page=page,
                per_page=per_page,
                period_from=period_from,
                period_to=period_to,
            ),
        }

    @router.get("/api/sender/campaign-full-analytics/{job_id}")
    def sender_campaign_full_analytics(
        job_id: str,
        refresh: bool = False,
        period_from: str = "",
        period_to: str = "",
        delivery_page: int = Query(1, ge=1),
        sent_log_page: int = Query(1, ge=1),
        attempts_page: int = Query(1, ge=1),
        documents_page: int = Query(1, ge=1),
        documents_q: str = Query(""),
        per_page: int = Query(50, ge=1, le=200),
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=False)
        period_from, period_to = _validate_statistics_period(period_from, period_to)
        return {
            "status": "ok",
            "result": build_campaign_full_analytics(
                job_id,
                refresh=refresh,
                period_from=period_from,
                period_to=period_to,
                delivery_page=delivery_page,
                sent_log_page=sent_log_page,
                attempts_page=attempts_page,
                documents_page=documents_page,
                documents_q=documents_q,
                per_page=per_page,
            ),
        }

    @router.get("/api/sender/reports")
    def sender_reports(
        job_id: str | None = None,
        principal: object = Depends(check_auth),
    ):
        job_ids = _resolve_job_ids(principal, job_id=job_id)
        return {"status": "ok", "result": build_reports_view(job_ids)}

    @router.post("/api/sender/reports/export")
    def sender_reports_export(
        payload: ReportExportRequest = Body(...),
        principal: object = Depends(check_auth),
    ):
        target_job_id = payload.job_id
        if target_job_id:
            ensure_job_access(target_job_id, principal, allow_missing=False)
        else:
            job_ids = _resolve_job_ids(principal)
            target_job_id = job_ids[0] if job_ids else None
        if not target_job_id:
            raise HTTPException(status_code=404, detail="Нет доступных рассылок для формирования отчёта.")
        try:
            result = export_report(
                target_job_id,
                report_type=payload.report_type,
                fmt=payload.fmt,
                period_from=payload.period_from,
                period_to=payload.period_to,
                author=_principal_name(principal),
                options=payload.options,
            )
        except Exception as exc:
            logger.exception("sender_report_export_failed", job_id=target_job_id)
            raise internal_server_error("Не удалось сформировать отчёт.") from exc
        return {"status": "ok", "result": result}

    @router.get("/api/sender/reports/download/{report_id}")
    def sender_reports_download(report_id: str, principal: object = Depends(check_auth)):
        job_ids = _resolve_job_ids(principal)
        path = find_report_file(job_ids, report_id)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Отчёт не найден.")
        media_types = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".csv": "text/csv",
            ".ndjson": "application/x-ndjson",
        }
        suffix = path.suffix.lower()
        return FileResponse(
            path,
            media_type=media_types.get(suffix, "application/octet-stream"),
            filename=path.name,
            headers=DOWNLOAD_HEADERS,
        )

    @router.get("/api/sender/manager-dashboard")
    def sender_manager_dashboard(
        job_id: str | None = None,
        campaign: str | None = None,
        period_from: str = "",
        period_to: str = "",
        provider: str | None = None,
        providers: str | None = None,
        status: str | None = None,
        recipient_role: str | None = None,
        consent_status: str = "",
        manager_action: str = "",
        organization: str = "",
        problems_only: bool = False,
        q: str = "",
        refresh: bool = False,
        principal: object = Depends(check_auth),
    ):
        filters = _build_filters(
            principal,
            job_id=job_id,
            campaign=campaign,
            period_from=period_from,
            period_to=period_to,
            provider=provider,
            providers=providers,
            status=status,
            recipient_role=recipient_role,
            consent_status=consent_status,
            manager_action=manager_action,
            organization=organization,
            problems_only=problems_only,
            q=q,
        )
        return {"status": "ok", "result": build_manager_dashboard(filters, refresh=refresh)}

    @router.get("/api/sender/domain-delivery-stats")
    async def sender_domain_delivery_stats(
        job_id: str | None = None,
        period_from: str = "",
        period_to: str = "",
        principal: object = Depends(check_auth),
    ):
        filters = _build_filters(
            principal,
            job_id=job_id,
            period_from=period_from,
            period_to=period_to,
        )
        return {"status": "ok", "result": build_domain_delivery_stats(filters)}

    return router
