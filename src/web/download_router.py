from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.generator.generation.document_builder import OUTPUT_FOLDER_MANIFEST_FILENAME
from src.generator.delivery.sender_report import (
    build_sender_delivery_report_xlsx,
    sender_delivery_report_has_data,
)
from src.generator.inflection.inflection_report import load_inflection_log, save_inflection_csv, write_inflection_log_jsonl
from src.generator.knowledge.agent_memory import (
    build_agent_report,
    build_learning_candidates,
    build_quarantine_items,
    get_agent_memory_csv_path,
    get_agent_quarantine_csv_path,
    get_agent_report_path,
    save_agent_report,
    save_learning_memory_csv,
    save_quarantine_csv,
)
from src.generator.knowledge.correction_report import (
    build_correction_report_xlsx,
    correction_report_has_data,
)
from src.jobs import resolve_job_paths
from src.jobs.access import JobAccessDenied, authorize_job_access
from src.utils.logger import logger


DOWNLOAD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "Referrer-Policy": "no-referrer",
}


def legacy_parser_output_dir() -> Path:
    return Path(__file__).parents[2] / "src" / "parser_new" / "output" / "latest"


def download_response(path: Path, *, media_type: str, filename: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers=DOWNLOAD_HEADERS,
    )


def _downloadable_output_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != OUTPUT_FOLDER_MANIFEST_FILENAME
    ]


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
    def ensure_parser_table_verified(job_id: str | None) -> None:
        parser_state = get_parser_status(job_id)
        verification_state = parser_state.get("municipality_name_verification_state") or {}
        verification_result = parser_state.get("municipality_name_verification") or {}
        verification_completed = (
            str(verification_state.get("status") or "") == "completed"
            or (
                str(verification_result.get("status") or "") == "ok"
                and safe_int(verification_result.get("total_rows")) > 0
            )
        )
        if not verification_completed:
            raise HTTPException(status_code=409, detail="Дождитесь завершения проверки таблицы.")

    def ensure_local_job_path(job_id: str | None, relative_path: str) -> Path:
        from src.jobs.storage import normalize_job_id
        from src.jobs.workspace import ensure_local_file

        paths = resolve_job_paths(job_id)
        if not normalize_job_id(job_id):
            return paths.root_dir / relative_path
        try:
            return ensure_local_file(job_id, relative_path)
        except FileNotFoundError:
            # Object legitimately absent in the store; fall back to local path.
            return paths.root_dir / relative_path
        except Exception:
            logger.warning(
                "ensure_local_job_path_failed",
                job_id=job_id,
                relative_path=relative_path,
                exc_info=True,
            )
            return paths.root_dir / relative_path

    def pull_job_workspace(job_id: str | None, subdirs: list[str]) -> None:
        from src.jobs.storage import normalize_job_id
        from src.jobs.workspace import pull_job

        if not normalize_job_id(job_id):
            return
        try:
            pull_job(job_id, subdirs)
        except ValueError:
            pass

    @router.get("/api/download/output")
    async def download_output(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        pull_job_workspace(job_id, ["output", "archives"])
        output_dir = resolve_job_paths(job_id).output_dir
        if not output_dir.exists():
            raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")

        if not _downloadable_output_files(output_dir):
            raise HTTPException(status_code=404, detail="Готовые документы не найдены. Повторите подготовку документов.")

        if output_archive_ready is not None and not output_archive_ready(job_id):
            raise HTTPException(status_code=409, detail="Документы ещё собираются. Дождитесь завершения подготовки и повторите скачивание.")

        archive_path, cache_is_fresh = resolve_cached_output_archive(job_id)
        if cache_is_fresh and (not archive_path.exists() or archive_path.stat().st_size <= 22):
            cache_is_fresh = False
        if not cache_is_fresh:
            try:
                archive_path = build_output_archive(job_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Готовые документы не найдены. Повторите подготовку документов.") from exc

        return download_response(
            archive_path,
            media_type="application/zip",
            filename="output.zip",
        )

    @router.get("/api/download/data-xlsx")
    async def download_data_xlsx(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        job_paths = resolve_job_paths(job_id)
        from src.jobs.clients_store import prepare_data_xlsx

        ensure_local_job_path(job_id, "input/data.xlsx")
        data_path = prepare_data_xlsx(job_id, job_paths.data_xlsx)
        if not data_path.exists():
            raise HTTPException(status_code=404, detail="Файл data.xlsx не найден.")
        return download_response(
            data_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="data.xlsx",
        )

    @router.get("/api/parser/download-result")
    async def download_parser_result(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        ensure_parser_table_verified(job_id)
        if job_id:
            paths = resolve_job_paths(job_id)
            latest = latest_matching_file(
                [paths.output_dir], pattern="batch_*.xlsx", exclude_substring="FAILED"
            )
            if latest is None:
                raise HTTPException(status_code=404, detail="Файл результата не найден")
            return download_response(
                latest,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=latest.name,
            )

        parser_output = legacy_parser_output_dir()
        latest = latest_matching_file(
            [parser_output], pattern="batch_*.xlsx", exclude_substring="FAILED"
        )
        if latest is None:
            raise HTTPException(status_code=404, detail="Файл результата не найден")

        return download_response(
            latest,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=latest.name,
        )

    @router.get("/api/parser/download-failed")
    async def download_parser_failed(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        ensure_parser_table_verified(job_id)
        if job_id:
            paths = resolve_job_paths(job_id)
            search_dirs = [paths.output_dir] if paths.output_dir.exists() else []
        else:
            parser_output = legacy_parser_output_dir()
            search_dirs = [parser_output] if parser_output.exists() else []

        latest = latest_matching_file(search_dirs, pattern="*FAILED*.xlsx")
        if latest is None:
            raise HTTPException(status_code=404, detail="Файл непроверенных не найден")

        return download_response(
            latest,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=latest.name,
        )

    @router.get("/api/download/sent-mail-log")
    async def download_sent_mail_log(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        from src.jobs.job_docs import write_sent_mail_log_jsonl

        job_paths = resolve_job_paths(job_id)
        log_path = job_paths.sent_mail_log_path
        try:
            write_sent_mail_log_jsonl(job_id, log_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Журнал отправленных писем пока не создан.") from exc
        return download_response(
            log_path,
            media_type="application/x-ndjson",
            filename="sent_mail_log.jsonl",
        )

    @router.get("/api/download/sender-delivery-report")
    async def download_sender_delivery_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        if not sender_delivery_report_has_data(job_id):
            raise HTTPException(
                status_code=404,
                detail="Журнал отправки пока пуст. Сначала запустите реальную отправку через UniSender, RuSender или MailoPost.",
            )
        job_paths = resolve_job_paths(job_id)
        report_path = job_state_dir(job_id) / "sender_delivery_report.xlsx"
        pull_job_workspace(job_id, ["output"])
        if not is_cache_fresh(report_path, [], max_age_seconds=180):
            report_path = build_sender_delivery_report_xlsx(job_id, refresh=True)
        return download_response(
            report_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="sender_delivery_report.xlsx",
        )

    @router.get("/api/download/inflection-log")
    async def download_inflection_log(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        job_paths = resolve_job_paths(job_id)
        log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
        try:
            write_inflection_log_jsonl(job_id, log_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Журнал склонений пока не создан.") from exc
        return download_response(
            log_path,
            media_type="application/x-ndjson",
            filename="inflection_log.jsonl",
        )

    @router.get("/api/download/inflection-report")
    async def download_inflection_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        rows = load_inflection_log(job_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Журнал склонений пока не создан.")
        job_paths = resolve_job_paths(job_id)
        report_path = job_paths.root_dir / "state" / "inflection_report.csv"
        save_inflection_csv(rows, report_path)
        return download_response(
            report_path,
            media_type="text/csv",
            filename="inflection_report.csv",
        )

    @router.get("/api/download/agent-memory")
    async def download_agent_memory(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        candidates = build_learning_candidates(job_id)
        if not candidates:
            raise HTTPException(status_code=404, detail="Кандидаты для памяти агента пока не найдены.")
        report_path = get_agent_memory_csv_path(job_id)
        save_learning_memory_csv(candidates, report_path)
        return download_response(
            report_path,
            media_type="text/csv",
            filename="agent_memory_candidates.csv",
        )

    @router.get("/api/download/agent-quarantine")
    async def download_agent_quarantine(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        items = build_quarantine_items(job_id)
        if not items:
            raise HTTPException(status_code=404, detail="Карантин агента пока пуст.")
        report_path = get_agent_quarantine_csv_path(job_id)
        save_quarantine_csv(items, report_path)
        return download_response(
            report_path,
            media_type="text/csv",
            filename="agent_quarantine.csv",
        )

    @router.get("/api/download/agent-report")
    async def download_agent_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        report_text = build_agent_report(job_id)
        if not report_text.strip():
            raise HTTPException(status_code=404, detail="Отчет агента пока пуст.")
        report_path = get_agent_report_path(job_id)
        save_agent_report(job_id)
        return download_response(
            report_path,
            media_type="text/plain; charset=utf-8",
            filename="agent_report.txt",
        )

    @router.get("/api/download/correction-report")
    async def download_correction_report(job_id: str | None = None, principal: object = Depends(check_auth)):
        ensure_job_access(job_id, principal, allow_missing=True)
        if not correction_report_has_data(job_id):
            raise HTTPException(status_code=404, detail="Журнал исправлений пока пуст. Сначала запустите генератор/филолога.")
        report_path = job_state_dir(job_id) / "journal_corrections_report.xlsx"
        source_paths = [
            job_state_dir(job_id) / "philologist.json",
            job_state_dir(job_id) / "inflection_log.jsonl",
        ]
        if not is_cache_fresh(report_path, source_paths):
            report_path = build_correction_report_xlsx(job_id)
        return download_response(
            report_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="journal_corrections_report.xlsx",
        )

    return router


