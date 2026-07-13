from __future__ import annotations

from typing import Callable
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.jobs.access import JobAccessDenied, authorize_job_access
from src.web.download_sources import (
    DOWNLOAD_HEADERS,
    DownloadResolutionError,
    PreviewMode,
    download_source_meta,
    list_output_archive_entries,
    normalize_download_kind,
    resolve_download_path,
    resolve_output_file_path,
)
from src.web.preview_service import (
    MAX_ARCHIVE_PAGE,
    MAX_TABLE_ROWS,
    read_ndjson_preview,
    read_table_preview,
    read_text_preview,
)
from src.web.responses import ok_response


def create_preview_router(
    *,
    check_auth: Callable,
    latest_matching_file: Callable[..., Path | None],
    is_cache_fresh: Callable[..., bool],
    job_state_dir: Callable[[str | None], Path],
    get_parser_status: Callable[[str | None], dict],
    safe_int: Callable[..., int],
    resolve_cached_output_archive: Callable[[str | None], tuple[Path, bool]],
    build_output_archive: Callable[[str | None], Path],
    output_archive_ready: Callable[[str | None], bool] | None = None,
) -> APIRouter:
    router = APIRouter()

    def ensure_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False) -> None:
        try:
            authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def resolve_or_http(kind: str, job_id: str | None, *, for_preview: bool = True):
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

    def normalize_kind_or_http(kind: str) -> str:
        try:
            return normalize_download_kind(kind)
        except DownloadResolutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    def build_download_url(meta_path: str, job_id: str | None) -> str:
        query: dict[str, str] = {}
        if job_id:
            query["job_id"] = job_id
        if not query:
            return meta_path
        return f"{meta_path}?{urlencode(query)}"

    @router.get("/api/preview/meta")
    def preview_meta(
        kind: str,
        job_id: str | None = None,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        normalized = normalize_kind_or_http(kind)
        meta = download_source_meta(normalized)
        resolved = resolve_or_http(normalized, job_id, for_preview=True)
        payload: dict = {
            "kind": meta.kind,
            "title": meta.title,
            "preview_mode": meta.preview_mode.value,
            "download_url": build_download_url(meta.download_path, job_id),
            "page_size": MAX_TABLE_ROWS,
            "archive_page_size": MAX_ARCHIVE_PAGE,
            "filename": resolved.meta.filename,
        }
        if meta.preview_mode == PreviewMode.ARCHIVE and resolved.output_dir is not None:
            _, total_files = list_output_archive_entries(resolved.output_dir, offset=0, limit=1)
            payload["total_files"] = total_files
        return ok_response(payload, **payload)

    @router.get("/api/preview/table")
    def preview_table(
        kind: str,
        job_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        sheet: int = 0,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        normalized = normalize_kind_or_http(kind)
        meta = download_source_meta(normalized)
        if meta.preview_mode not in {PreviewMode.TABLE, PreviewMode.NDJSON}:
            raise HTTPException(status_code=400, detail="Этот файл не поддерживает табличный предпросмотр.")
        resolved = resolve_or_http(normalized, job_id, for_preview=True)
        if meta.preview_mode == PreviewMode.NDJSON:
            result = read_ndjson_preview(resolved.path, offset=offset, limit=limit)
        else:
            try:
                result = read_table_preview(
                    resolved.path,
                    preview_mode=meta.preview_mode,
                    sheet_index=sheet,
                    offset=offset,
                    limit=limit,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ok_response(result, **result)

    @router.get("/api/preview/text")
    def preview_text(
        kind: str,
        job_id: str | None = None,
        offset: int = 0,
        limit: int = 500,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        normalized = normalize_kind_or_http(kind)
        meta = download_source_meta(normalized)
        if meta.preview_mode not in {PreviewMode.TEXT, PreviewMode.NDJSON}:
            raise HTTPException(status_code=400, detail="Этот файл не поддерживает текстовый предпросмотр.")
        resolved = resolve_or_http(normalized, job_id, for_preview=True)
        if meta.preview_mode == PreviewMode.NDJSON:
            result = read_ndjson_preview(resolved.path, offset=offset, limit=limit)
            return ok_response(result, **result)
        result = read_text_preview(resolved.path, offset=offset, limit=limit)
        return ok_response(result, **result)

    @router.get("/api/preview/archive")
    def preview_archive(
        kind: str = "output",
        job_id: str | None = None,
        offset: int = 0,
        limit: int = MAX_ARCHIVE_PAGE,
        q: str = "",
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        normalized = normalize_kind_or_http(kind)
        meta = download_source_meta(normalized)
        if meta.preview_mode != PreviewMode.ARCHIVE:
            raise HTTPException(status_code=400, detail="Этот файл не поддерживает архивный предпросмотр.")
        resolved = resolve_or_http(normalized, job_id, for_preview=True)
        if resolved.output_dir is None:
            raise HTTPException(status_code=404, detail="Каталог output не найден.")
        entries, total = list_output_archive_entries(
            resolved.output_dir,
            offset=max(0, offset),
            limit=min(max(1, limit), MAX_ARCHIVE_PAGE),
            query=q,
        )
        payload = {"entries": entries, "total": total, "offset": max(0, offset), "limit": limit}
        return ok_response(payload, **payload)

    @router.get("/api/preview/file")
    def preview_file(
        kind: str = "output",
        path: str = "",
        job_id: str | None = None,
        principal: object = Depends(check_auth),
    ):
        ensure_job_access(job_id, principal, allow_missing=True)
        normalized = normalize_kind_or_http(kind)
        meta = download_source_meta(normalized)
        if meta.preview_mode != PreviewMode.ARCHIVE:
            raise HTTPException(status_code=400, detail="Предпросмотр файла доступен только для output.")
        resolved = resolve_or_http(normalized, job_id, for_preview=True)
        if resolved.output_dir is None:
            raise HTTPException(status_code=404, detail="Каталог output не найден.")
        try:
            file_path = resolve_output_file_path(resolved.output_dir, path)
        except DownloadResolutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        suffix = file_path.suffix.lower()
        media_type = "application/octet-stream"
        headers = dict(DOWNLOAD_HEADERS)
        if suffix == ".pdf":
            media_type = "application/pdf"
            encoded_filename = quote(file_path.name)
            headers["Content-Disposition"] = (
                f"inline; filename=preview.pdf; filename*=UTF-8''{encoded_filename}"
            )
            return FileResponse(file_path, media_type=media_type, headers=headers)
        if suffix == ".docx":
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            return FileResponse(
                file_path,
                media_type=media_type,
                filename=file_path.name,
                headers=headers,
            )
        raise HTTPException(status_code=400, detail="Предпросмотр поддерживается только для PDF и DOCX.")

    return router
