from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.web.download_sources import (
    DOWNLOAD_HEADERS,
    DownloadResolutionError,
    legacy_parser_output_dir,
    resolve_download_path,
)


def download_response(path: Path, *, media_type: str, filename: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers=DOWNLOAD_HEADERS,
    )


def create_download_router(
    *,
    check_auth: Callable,
    prefer_existing_file: Callable[[Path, Path], Path],
    latest_matching_file: Callable[..., Path | None],
    resolve_cached_output_archive: Callable[[str | None], tuple[Path, bool]],
    build_output_archive: Callable[[str | None], Path],
    is_cache_fresh: Callable[..., bool],
    job_state_dir: Callable[[str | None], Path],
    get_parser_status: Callable[[str | None], dict],
    safe_int: Callable[..., int],
    output_archive_ready: Callable[[str | None], bool] | None = None,
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def resolve_or_http(kind: str, job_id: str | None, *, for_preview: bool = False):
        try:
            return resolve_download_path(
                kind,
                job_id,
                latest_matching_file=latest_matching_file,
                is_cache_fresh=is_cache_fresh,
                job_state_dir=job_state_dir,
                get_parser_status=get_parser_status,
                safe_int=safe_int,
                resolve_cached_output_archive=resolve_cached_output_archive,
                build_output_archive=build_output_archive,
                output_archive_ready=output_archive_ready,
                for_preview=for_preview,
            )
        except DownloadResolutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/api/download/output")
    async def download_output(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("output", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/data-xlsx")
    async def download_data_xlsx(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("data-xlsx", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/parser/download-result")
    async def download_parser_result(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("parser-result", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/parser/download-failed")
    async def download_parser_failed(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("parser-failed", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/sent-mail-log")
    async def download_sent_mail_log(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("sent-mail-log", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/sender-delivery-report")
    async def download_sender_delivery_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("sender-delivery-report", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/inflection-log")
    async def download_inflection_log(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("inflection-log", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/inflection-report")
    async def download_inflection_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("inflection-report", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/agent-memory")
    async def download_agent_memory(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("agent-memory", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/agent-quarantine")
    async def download_agent_quarantine(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("agent-quarantine", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/agent-report")
    async def download_agent_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("agent-report", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    @router.get("/api/download/correction-report")
    async def download_correction_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        resolved = resolve_or_http("correction-report", job_id)
        return download_response(
            resolved.path,
            media_type=resolved.meta.media_type,
            filename=resolved.meta.filename,
        )

    return router


__all__ = ["create_download_router", "download_response", "legacy_parser_output_dir"]
