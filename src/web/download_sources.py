from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from src.generator.delivery.sender_report import (
    build_sender_delivery_report_xlsx,
    sender_delivery_report_has_data,
)
from src.generator.generation.document_builder import (
    OUTPUT_FOLDER_MANIFEST_FILENAME,
    read_output_folder_manifest,
)
from src.generator.inflection.inflection_report import (
    load_inflection_log,
    save_inflection_csv,
    write_inflection_log_jsonl,
)
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


class PreviewMode(StrEnum):
    ARCHIVE = "archive"
    TABLE = "table"
    NDJSON = "ndjson"
    TEXT = "text"


DOWNLOAD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "Referrer-Policy": "no-referrer",
}

DOWNLOAD_KIND_ALIASES = {
    "output": "output",
    "data-xlsx": "data-xlsx",
    "parser-result": "parser-result",
    "parser-failed": "parser-failed",
    "correction-report": "correction-report",
    "sender-delivery-report": "sender-delivery-report",
    "sent-mail-log": "sent-mail-log",
    "inflection-log": "inflection-log",
    "inflection-report": "inflection-report",
    "agent-memory": "agent-memory",
    "agent-quarantine": "agent-quarantine",
    "agent-report": "agent-report",
}


@dataclass(frozen=True)
class DownloadSourceMeta:
    kind: str
    preview_mode: PreviewMode
    download_path: str
    title: str
    media_type: str
    filename: str


@dataclass(frozen=True)
class ResolvedDownload:
    path: Path
    meta: DownloadSourceMeta
    output_dir: Path | None = None


class DownloadResolutionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def legacy_parser_output_dir() -> Path:
    return Path(__file__).parents[2] / "src" / "parser_new" / "output" / "latest"


def normalize_download_kind(kind: str | None) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized not in DOWNLOAD_KIND_ALIASES:
        raise DownloadResolutionError(400, f"Неизвестный тип файла: {kind}")
    return DOWNLOAD_KIND_ALIASES[normalized]


def download_source_meta(kind: str) -> DownloadSourceMeta:
    normalized = normalize_download_kind(kind)
    mapping: dict[str, DownloadSourceMeta] = {
        "output": DownloadSourceMeta(
            kind="output",
            preview_mode=PreviewMode.ARCHIVE,
            download_path="/api/download/output",
            title="Готовые документы",
            media_type="application/zip",
            filename="output.zip",
        ),
        "data-xlsx": DownloadSourceMeta(
            kind="data-xlsx",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/download/data-xlsx",
            title="Таблица data.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="data.xlsx",
        ),
        "parser-result": DownloadSourceMeta(
            kind="parser-result",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/parser/download-result",
            title="Результат парсера",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="batch_result.xlsx",
        ),
        "parser-failed": DownloadSourceMeta(
            kind="parser-failed",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/parser/download-failed",
            title="Непроверенные строки",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="batch_failed.xlsx",
        ),
        "correction-report": DownloadSourceMeta(
            kind="correction-report",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/download/correction-report",
            title="Отчёт исправлений",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="journal_corrections_report.xlsx",
        ),
        "sender-delivery-report": DownloadSourceMeta(
            kind="sender-delivery-report",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/download/sender-delivery-report",
            title="Отчёт отправки",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="sender_delivery_report.xlsx",
        ),
        "sent-mail-log": DownloadSourceMeta(
            kind="sent-mail-log",
            preview_mode=PreviewMode.NDJSON,
            download_path="/api/download/sent-mail-log",
            title="Журнал отправленных писем",
            media_type="application/x-ndjson",
            filename="sent_mail_log.jsonl",
        ),
        "inflection-log": DownloadSourceMeta(
            kind="inflection-log",
            preview_mode=PreviewMode.NDJSON,
            download_path="/api/download/inflection-log",
            title="Журнал склонений",
            media_type="application/x-ndjson",
            filename="inflection_log.jsonl",
        ),
        "inflection-report": DownloadSourceMeta(
            kind="inflection-report",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/download/inflection-report",
            title="Отчёт склонений",
            media_type="text/csv",
            filename="inflection_report.csv",
        ),
        "agent-memory": DownloadSourceMeta(
            kind="agent-memory",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/download/agent-memory",
            title="Кандидаты памяти агента",
            media_type="text/csv",
            filename="agent_memory_candidates.csv",
        ),
        "agent-quarantine": DownloadSourceMeta(
            kind="agent-quarantine",
            preview_mode=PreviewMode.TABLE,
            download_path="/api/download/agent-quarantine",
            title="Карантин агента",
            media_type="text/csv",
            filename="agent_quarantine.csv",
        ),
        "agent-report": DownloadSourceMeta(
            kind="agent-report",
            preview_mode=PreviewMode.TEXT,
            download_path="/api/download/agent-report",
            title="Отчёт агента",
            media_type="text/plain; charset=utf-8",
            filename="agent_report.txt",
        ),
    }
    return mapping[normalized]


def downloadable_output_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != OUTPUT_FOLDER_MANIFEST_FILENAME
    ]


def ensure_local_job_path(job_id: str | None, relative_path: str) -> Path:
    from src.jobs.storage import normalize_job_id
    from src.jobs.workspace import ensure_local_file

    paths = resolve_job_paths(job_id)
    if not normalize_job_id(job_id):
        return paths.root_dir / relative_path
    try:
        return ensure_local_file(job_id, relative_path)
    except Exception:
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


def parser_table_verified(
    job_id: str | None,
    *,
    get_parser_status: Callable[[str | None], dict],
    safe_int: Callable[..., int],
) -> bool:
    parser_state = get_parser_status(job_id)
    verification_state = parser_state.get("municipality_name_verification_state") or {}
    verification_result = parser_state.get("municipality_name_verification") or {}
    return (
        str(verification_state.get("status") or "") == "completed"
        or (
            str(verification_result.get("status") or "") == "ok"
            and safe_int(verification_result.get("total_rows")) > 0
        )
    )


def resolve_download_path(
    kind: str,
    job_id: str | None,
    *,
    latest_matching_file: Callable[..., Path | None],
    is_cache_fresh: Callable[..., bool],
    job_state_dir: Callable[[str | None], Path],
    get_parser_status: Callable[[str | None], dict],
    safe_int: Callable[..., int],
    resolve_cached_output_archive: Callable[[str | None], tuple[Path, bool]] | None = None,
    build_output_archive: Callable[[str | None], Path] | None = None,
    output_archive_ready: Callable[[str | None], bool] | None = None,
    for_preview: bool = False,
) -> ResolvedDownload:
    normalized = normalize_download_kind(kind)
    meta = download_source_meta(normalized)

    if normalized == "output":
        pull_job_workspace(job_id, ["output", "archives"])
        output_dir = resolve_job_paths(job_id).output_dir
        if not output_dir.exists():
            raise DownloadResolutionError(404, "Файлы не найдены. Сначала запустите генерацию.")
        if not downloadable_output_files(output_dir):
            raise DownloadResolutionError(404, "Готовые документы не найдены. Повторите подготовку документов.")
        if for_preview:
            return ResolvedDownload(path=output_dir, meta=meta, output_dir=output_dir)
        if output_archive_ready is not None and not output_archive_ready(job_id):
            raise DownloadResolutionError(
                409,
                "Документы ещё собираются. Дождитесь завершения подготовки и повторите скачивание.",
            )
        if resolve_cached_output_archive is None or build_output_archive is None:
            raise DownloadResolutionError(500, "Архив output недоступен.")
        archive_path, cache_is_fresh = resolve_cached_output_archive(job_id)
        if cache_is_fresh and (not archive_path.exists() or archive_path.stat().st_size <= 22):
            cache_is_fresh = False
        if not cache_is_fresh:
            try:
                archive_path = build_output_archive(job_id)
            except FileNotFoundError as exc:
                raise DownloadResolutionError(
                    404,
                    "Готовые документы не найдены. Повторите подготовку документов.",
                ) from exc
        return ResolvedDownload(path=archive_path, meta=meta, output_dir=output_dir)

    if normalized == "data-xlsx":
        job_paths = resolve_job_paths(job_id)
        from src.jobs.clients_store import prepare_data_xlsx

        ensure_local_job_path(job_id, "input/data.xlsx")
        data_path = prepare_data_xlsx(job_id, job_paths.data_xlsx)
        if not data_path.exists():
            raise DownloadResolutionError(404, "Файл data.xlsx не найден.")
        return ResolvedDownload(path=data_path, meta=meta)

    if normalized in {"parser-result", "parser-failed"}:
        if not parser_table_verified(job_id, get_parser_status=get_parser_status, safe_int=safe_int):
            raise DownloadResolutionError(409, "Дождитесь завершения проверки таблицы.")
        if normalized == "parser-result":
            if job_id:
                paths = resolve_job_paths(job_id)
                latest = latest_matching_file(
                    [paths.output_dir], pattern="batch_*.xlsx", exclude_substring="FAILED"
                )
            else:
                parser_output = legacy_parser_output_dir()
                latest = latest_matching_file(
                    [parser_output], pattern="batch_*.xlsx", exclude_substring="FAILED"
                )
            if latest is None:
                raise DownloadResolutionError(404, "Файл результата не найден")
            meta = DownloadSourceMeta(
                kind=meta.kind,
                preview_mode=meta.preview_mode,
                download_path=meta.download_path,
                title=meta.title,
                media_type=meta.media_type,
                filename=latest.name,
            )
            return ResolvedDownload(path=latest, meta=meta)

        if job_id:
            paths = resolve_job_paths(job_id)
            search_dirs = [paths.output_dir] if paths.output_dir.exists() else []
        else:
            parser_output = legacy_parser_output_dir()
            search_dirs = [parser_output] if parser_output.exists() else []
        latest = latest_matching_file(search_dirs, pattern="*FAILED*.xlsx")
        if latest is None:
            raise DownloadResolutionError(404, "Файл непроверенных не найден")
        meta = DownloadSourceMeta(
            kind=meta.kind,
            preview_mode=meta.preview_mode,
            download_path=meta.download_path,
            title=meta.title,
            media_type=meta.media_type,
            filename=latest.name,
        )
        return ResolvedDownload(path=latest, meta=meta)

    if normalized == "sent-mail-log":
        from src.jobs.job_docs import write_sent_mail_log_jsonl

        job_paths = resolve_job_paths(job_id)
        log_path = job_paths.sent_mail_log_path
        try:
            write_sent_mail_log_jsonl(job_id, log_path)
        except FileNotFoundError as exc:
            raise DownloadResolutionError(404, "Журнал отправленных писем пока не создан.") from exc
        return ResolvedDownload(path=log_path, meta=meta)

    if normalized == "sender-delivery-report":
        if not sender_delivery_report_has_data(job_id):
            raise DownloadResolutionError(
                404,
                "Журнал отправки пока пуст. Сначала запустите реальную отправку через UniSender, RuSender или MailoPost.",
            )
        report_path = job_state_dir(job_id) / "sender_delivery_report.xlsx"
        pull_job_workspace(job_id, ["output"])
        if not is_cache_fresh(report_path, [], max_age_seconds=180):
            report_path = build_sender_delivery_report_xlsx(job_id, refresh=True)
        return ResolvedDownload(path=report_path, meta=meta)

    if normalized == "inflection-log":
        job_paths = resolve_job_paths(job_id)
        log_path = job_paths.root_dir / "state" / "inflection_log.jsonl"
        try:
            write_inflection_log_jsonl(job_id, log_path)
        except FileNotFoundError as exc:
            raise DownloadResolutionError(404, "Журнал склонений пока не создан.") from exc
        return ResolvedDownload(path=log_path, meta=meta)

    if normalized == "inflection-report":
        rows = load_inflection_log(job_id)
        if not rows:
            raise DownloadResolutionError(404, "Журнал склонений пока не создан.")
        job_paths = resolve_job_paths(job_id)
        report_path = job_paths.root_dir / "state" / "inflection_report.csv"
        save_inflection_csv(rows, report_path)
        return ResolvedDownload(path=report_path, meta=meta)

    if normalized == "agent-memory":
        candidates = build_learning_candidates(job_id)
        if not candidates:
            raise DownloadResolutionError(404, "Кандидаты для памяти агента пока не найдены.")
        report_path = get_agent_memory_csv_path(job_id)
        save_learning_memory_csv(candidates, report_path)
        return ResolvedDownload(path=report_path, meta=meta)

    if normalized == "agent-quarantine":
        items = build_quarantine_items(job_id)
        if not items:
            raise DownloadResolutionError(404, "Карантин агента пока пуст.")
        report_path = get_agent_quarantine_csv_path(job_id)
        save_quarantine_csv(items, report_path)
        return ResolvedDownload(path=report_path, meta=meta)

    if normalized == "agent-report":
        report_text = build_agent_report(job_id)
        if not report_text.strip():
            raise DownloadResolutionError(404, "Отчет агента пока пуст.")
        report_path = get_agent_report_path(job_id)
        save_agent_report(job_id)
        return ResolvedDownload(path=report_path, meta=meta)

    if normalized == "correction-report":
        if not correction_report_has_data(job_id):
            raise DownloadResolutionError(
                404,
                "Журнал исправлений пока пуст. Сначала запустите генератор/филолога.",
            )
        report_path = job_state_dir(job_id) / "journal_corrections_report.xlsx"
        source_paths = [
            job_state_dir(job_id) / "philologist.json",
            job_state_dir(job_id) / "inflection_log.jsonl",
        ]
        if not is_cache_fresh(report_path, source_paths):
            report_path = build_correction_report_xlsx(job_id)
        return ResolvedDownload(path=report_path, meta=meta)

    raise DownloadResolutionError(400, f"Неизвестный тип файла: {kind}")


def resolve_output_file_path(output_dir: Path, relative_path: str) -> Path:
    normalized = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in normalized.split("/"):
        raise DownloadResolutionError(400, "Недопустимый путь к файлу.")
    candidate = (output_dir / normalized).resolve()
    output_root = output_dir.resolve()
    if output_root not in candidate.parents and candidate != output_root:
        raise DownloadResolutionError(400, "Недопустимый путь к файлу.")
    if not candidate.exists() or not candidate.is_file():
        raise DownloadResolutionError(404, "Файл не найден.")
    if candidate.name == OUTPUT_FOLDER_MANIFEST_FILENAME:
        raise DownloadResolutionError(404, "Файл не найден.")
    return candidate


def archive_entry_label(output_dir: Path, file_path: Path) -> str:
    relative = file_path.relative_to(output_dir)
    folder = relative.parent
    if folder == Path("."):
        return file_path.name
    manifest = read_output_folder_manifest(output_dir / folder)
    mun_name = str(manifest.get("mun_name") or "").strip()
    if mun_name:
        return f"{mun_name} — {file_path.name}"
    return str(relative).replace("\\", "/")


def list_output_archive_entries(
    output_dir: Path,
    *,
    offset: int = 0,
    limit: int = 100,
    query: str = "",
) -> tuple[list[dict], int]:
    files = downloadable_output_files(output_dir)
    entries: list[dict] = []
    normalized_query = str(query or "").strip().lower()
    for file_path in sorted(files, key=lambda path: str(path.relative_to(output_dir)).lower()):
        relative = str(file_path.relative_to(output_dir)).replace("\\", "/")
        label = archive_entry_label(output_dir, file_path)
        if normalized_query and normalized_query not in label.lower() and normalized_query not in relative.lower():
            continue
        entries.append(
            {
                "path": relative,
                "name": file_path.name,
                "ext": file_path.suffix.lower(),
                "size": file_path.stat().st_size,
                "label": label,
            }
        )
    total = len(entries)
    page = entries[offset : offset + limit]
    return page, total
