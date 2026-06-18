from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.generator.delivery.sender_report import (
    build_sender_delivery_report_xlsx,
    sender_delivery_report_has_data,
)
from src.generator.inflection.inflection_report import load_inflection_log, save_inflection_csv
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
) -> APIRouter:
    router = APIRouter()

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

    @router.get("/api/download/output")
    async def download_output(job_id: str | None = None, username: str = Depends(check_auth)):
        output_dir = resolve_job_paths(job_id).output_dir
        if not output_dir.exists():
            raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")

        archive_path, cache_is_fresh = resolve_cached_output_archive(job_id)
        if not cache_is_fresh:
            if not any(output_dir.rglob("*.*")):
                raise HTTPException(status_code=404, detail="Файлы не найдены. Сначала запустите генерацию.")
            archive_path = build_output_archive(job_id)

        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename="output.zip",
        )

    @router.get("/api/download/data-xlsx")
    async def download_data_xlsx(job_id: str | None = None, username: str = Depends(check_auth)):
        data_path = prefer_existing_file(resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
        if not data_path.exists():
            raise HTTPException(status_code=404, detail="Файл data.xlsx не найден.")
        return FileResponse(
            data_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="data.xlsx",
        )

    @router.get("/api/parser/download-result")
    async def download_parser_result(job_id: str | None = None, username: str = Depends(check_auth)):
        ensure_parser_table_verified(job_id)
        if job_id:
            try:
                paths = resolve_job_paths(job_id)
                latest = latest_matching_file(
                    [paths.output_dir], pattern="batch_*.xlsx", exclude_substring="FAILED"
                )
                if latest is not None:
                    return FileResponse(
                        latest,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename=latest.name,
                    )
            except Exception:
                pass

        parser_output = Path(__file__).parents[2] / "src" / "parser_new" / "output" / "latest"
        latest = latest_matching_file(
            [parser_output], pattern="batch_*.xlsx", exclude_substring="FAILED"
        )
        if latest is None:
            raise HTTPException(status_code=404, detail="Файл результата не найден")

        return FileResponse(
            latest,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=latest.name,
        )

    @router.get("/api/parser/download-failed")
    async def download_parser_failed(job_id: str | None = None, username: str = Depends(check_auth)):
        ensure_parser_table_verified(job_id)
        search_dirs: list[Path] = []

        try:
            paths = resolve_job_paths(job_id)
            if paths.output_dir.exists():
                search_dirs.append(paths.output_dir)
        except Exception:
            pass

        parser_output = Path(__file__).parents[2] / "src" / "parser_new" / "output" / "latest"
        if parser_output.exists():
            search_dirs.append(parser_output)

        latest = latest_matching_file(search_dirs, pattern="*FAILED*.xlsx")
        if latest is None:
            raise HTTPException(status_code=404, detail="Файл непроверенных не найден")

        return FileResponse(
            latest,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=latest.name,
        )

    @router.get("/api/download/sent-mail-log")
    async def download_sent_mail_log(job_id: str | None = None, username: str = Depends(check_auth)):
        job_paths = resolve_job_paths(job_id)
        log_path = (
            job_paths.sent_mail_log_path
            if not job_paths.uses_legacy_layout
            else Path("data/sent_mail_log.jsonl")
        )
        if not log_path.exists():
            raise HTTPException(status_code=404, detail="Журнал отправленных писем пока не создан.")
        return FileResponse(
            log_path,
            media_type="application/x-ndjson",
            filename="sent_mail_log.jsonl",
        )

    @router.get("/api/download/sender-delivery-report")
    async def download_sender_delivery_report(job_id: str | None = None, username: str = Depends(check_auth)):
        if not sender_delivery_report_has_data(job_id):
            raise HTTPException(
                status_code=404,
                detail="Журнал отправки пока пуст. Сначала запустите реальную отправку через UniSender или RuSender.",
            )
        job_paths = resolve_job_paths(job_id)
        report_path = job_state_dir(job_id) / "sender_delivery_report.xlsx"
        sent_log_path = (
            job_paths.sent_mail_log_path
            if not job_paths.uses_legacy_layout
            else Path("data/sent_mail_log.jsonl")
        )
        state_dir = job_state_dir(job_id)
        report_sources = [
            sent_log_path,
            state_dir / "rusender_events.jsonl",
            state_dir / "unisender_go_events.jsonl",
        ]
        if not is_cache_fresh(report_path, report_sources, max_age_seconds=180):
            report_path = build_sender_delivery_report_xlsx(job_id, refresh=True)
        return FileResponse(
            report_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="sender_delivery_report.xlsx",
        )

    @router.get("/api/download/inflection-log")
    async def download_inflection_log(job_id: str | None = None, username: str = Depends(check_auth)):
        job_paths = resolve_job_paths(job_id)
        log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
        if not log_path.exists():
            raise HTTPException(status_code=404, detail="Журнал склонений пока не создан.")
        return FileResponse(
            log_path,
            media_type="application/x-ndjson",
            filename="inflection_log.jsonl",
        )

    @router.get("/api/download/inflection-report")
    async def download_inflection_report(job_id: str | None = None, username: str = Depends(check_auth)):
        rows = load_inflection_log(job_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Журнал склонений пока не создан.")
        job_paths = resolve_job_paths(job_id)
        report_path = job_paths.root_dir / "state" / "inflection_report.csv"
        log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
        if not is_cache_fresh(report_path, [log_path]):
            save_inflection_csv(rows, report_path)
        return FileResponse(
            report_path,
            media_type="text/csv",
            filename="inflection_report.csv",
        )

    @router.get("/api/download/agent-memory")
    async def download_agent_memory(job_id: str | None = None, username: str = Depends(check_auth)):
        candidates = build_learning_candidates(job_id)
        if not candidates:
            raise HTTPException(status_code=404, detail="Кандидаты для памяти агента пока не найдены.")
        report_path = get_agent_memory_csv_path(job_id)
        save_learning_memory_csv(candidates, report_path)
        return FileResponse(
            report_path,
            media_type="text/csv",
            filename="agent_memory_candidates.csv",
        )

    @router.get("/api/download/agent-quarantine")
    async def download_agent_quarantine(job_id: str | None = None, username: str = Depends(check_auth)):
        items = build_quarantine_items(job_id)
        if not items:
            raise HTTPException(status_code=404, detail="Карантин агента пока пуст.")
        report_path = get_agent_quarantine_csv_path(job_id)
        save_quarantine_csv(items, report_path)
        return FileResponse(
            report_path,
            media_type="text/csv",
            filename="agent_quarantine.csv",
        )

    @router.get("/api/download/agent-report")
    async def download_agent_report(job_id: str | None = None, username: str = Depends(check_auth)):
        report_text = build_agent_report(job_id)
        if not report_text.strip():
            raise HTTPException(status_code=404, detail="Отчет агента пока пуст.")
        report_path = get_agent_report_path(job_id)
        save_agent_report(job_id)
        return FileResponse(
            report_path,
            media_type="text/plain; charset=utf-8",
            filename="agent_report.txt",
        )

    @router.get("/api/download/correction-report")
    async def download_correction_report(job_id: str | None = None, username: str = Depends(check_auth)):
        if not correction_report_has_data(job_id):
            raise HTTPException(status_code=404, detail="Журнал исправлений пока пуст. Сначала запустите генератор/филолога.")
        report_path = job_state_dir(job_id) / "journal_corrections_report.xlsx"
        source_paths = [
            job_state_dir(job_id) / "philologist.json",
            job_state_dir(job_id) / "inflection_log.jsonl",
        ]
        if not is_cache_fresh(report_path, source_paths):
            report_path = build_correction_report_xlsx(job_id)
        return FileResponse(
            report_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="journal_corrections_report.xlsx",
        )

    return router
